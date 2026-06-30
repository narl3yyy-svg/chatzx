//! chatxz v2 — Rust-primary server. Calls and media are native Rust;
//! RNS messaging runs in the Python backend (Reticulum) on an internal port.

mod proxy;
mod rns_client;

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, Method, StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use axum::routing::{any, get, post};
use axum::{Json, Router};
use chatxz_call::{CallManager, CallMode, SharedCallManager};
use chatxz_media::{MediaKind, FRAME_BYTES};
use chatxz_protocol::is_media_packet;
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::sync::broadcast;
use tracing::{error, info, warn};

#[derive(Clone)]
struct AppState {
    backend: String,
    static_root: PathBuf,
    calls: SharedCallManager,
    rns: rns_client::RnsClient,
    call_events: broadcast::Sender<String>,
    media_sockets: Arc<Mutex<HashMap<u64, String>>>,
    media_next_id: Arc<Mutex<u64>>,
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "chatxz_server=info,tower_http=warn".into()),
        )
        .init();

    let args: Vec<String> = std::env::args().collect();
    let port: u16 = parse_flag(&args, "--port")
        .and_then(|s| s.parse().ok())
        .unwrap_or(8742);
    let backend = parse_flag(&args, "--backend").unwrap_or("http://127.0.0.1:8743".into());
    let static_root = std::env::var("CHATXZ_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("chatxz/web/static");

    let rns = rns_client::RnsClient::new(&backend);
    let (call_tx, _) = broadcast::channel(64);

    let send_signaling = {
        let rns = rns.clone();
        Arc::new(move |peer: &str, payload: &str| {
            let rns = rns.clone();
            let peer = peer.to_string();
            let payload = payload.to_string();
            tokio::spawn(async move {
                if let Err(e) = rns.send_signaling(&peer, &payload).await {
                    warn!(%e, "rns signaling send failed");
                }
            });
        }) as chatxz_call::SendSignalingFn
    };

    let send_media = {
        let rns = rns.clone();
        Arc::new(move |peer: &str, data: &[u8]| {
            let rns = rns.clone();
            let peer = peer.to_string();
            let data = data.to_vec();
            tokio::spawn(async move {
                if let Err(e) = rns.send_media(&peer, &data).await {
                    warn!(%e, "rns media send failed");
                }
            });
        }) as chatxz_call::SendMediaFn
    };

    let peer_linked = Arc::new(|_peer: &str| true) as chatxz_call::PeerLinkedFn;

    let mut manager = CallManager::new(send_signaling, send_media, peer_linked);
    let events_tx = call_tx.clone();
    let rns_events = rns.clone();
    manager.set_event_handler(Arc::new(move |event, view| {
        let data = serde_json::to_value(view).unwrap_or_else(|_| json!({}));
        let msg = json!({
            "type": "call",
            "event": event,
            "data": data,
        });
        let _ = events_tx.send(msg.to_string());
        let rns = rns_events.clone();
        let event = event.to_string();
        tokio::spawn(async move {
            if let Err(e) = rns.post_call_event(&event, &data).await {
                warn!(%e, "call event forward failed");
            }
        });
    }));

    let state = AppState {
        backend: backend.clone(),
        static_root: static_root.clone(),
        calls: Arc::new(Mutex::new(manager)),
        rns,
        call_events: call_tx,
        media_sockets: Arc::new(Mutex::new(HashMap::new())),
        media_next_id: Arc::new(Mutex::new(0)),
    };

    let app = Router::new()
        .route("/api/call/:action", post(call_api))
        .route("/ws/media", get(media_ws))
        .route("/internal/signaling", post(internal_signaling))
        .route("/internal/media", post(internal_media))
        .route("/health", get(|| async { "ok" }))
        .fallback(any(proxy::forward))
        .with_state(state);

    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    info!(%addr, %backend, "chatxz-server v2 starting (Rust primary)");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, app).await.expect("serve");
}

fn parse_flag(args: &[String], flag: &str) -> Option<String> {
    args.iter()
        .position(|a| a == flag)
        .and_then(|i| args.get(i + 1).cloned())
}

#[derive(Deserialize)]
struct CallBody {
    peer: Option<String>,
    mode: Option<String>,
    call_id: Option<String>,
    muted: Option<bool>,
    video: Option<bool>,
    screen: Option<bool>,
    stats: Option<Value>,
}

async fn call_api(
    State(state): State<AppState>,
    Path(action): Path<String>,
    Json(body): Json<CallBody>,
) -> Json<Value> {
    let peer = body.peer.unwrap_or_default();

    match action.as_str() {
        "status" => {
            let linked = if peer.is_empty() {
                state.rns.any_linked().await.unwrap_or(false)
            } else {
                state.rns.peer_linked(&peer).await.unwrap_or(false)
            };
            let call = state.calls.lock().expect("calls").active_view();
            Json(json!({
                "status": "ok",
                "linked": linked,
                "call": call,
                "rust_media": true,
                "error": if linked || peer.is_empty() { Value::Null } else { json!("not_linked") },
            }))
        }
        "start" => {
            if peer.is_empty() {
                return Json(json!({"error": "peer required"}));
            }
            if !state.rns.peer_linked(&peer).await.unwrap_or(false) {
                return Json(json!({"error": "not_linked"}));
            }
            let mode = match body.mode.as_deref().unwrap_or("audio") {
                "video" => CallMode::Video,
                "screen" => CallMode::Screen,
                _ => CallMode::Audio,
            };
            let call = state.calls.lock().expect("calls").start_call(&peer, mode);
            match call {
                Some(c) => Json(json!({"status": "ok", "call": c})),
                None => Json(json!({"error": "busy"})),
            }
        }
        "accept" => {
            let ok = state
                .calls
                .lock()
                .expect("calls")
                .accept_call(body.call_id.as_deref().unwrap_or(""));
            Json(json!({"status": if ok { "ok" } else { "error" }}))
        }
        "reject" => {
            state
                .calls
                .lock()
                .expect("calls")
                .reject_call(body.call_id.as_deref().unwrap_or(""));
            Json(json!({"status": "ok"}))
        }
        "hangup" => {
            state.calls.lock().expect("calls").hangup(body.call_id.as_deref());
            Json(json!({"status": "ok"}))
        }
        "update" => {
            let call = state
                .calls
                .lock()
                .expect("calls")
                .update_call(body.muted, body.video, body.screen);
            Json(json!({"status": "ok", "call": call}))
        }
        _ => Json(json!({"error": "unknown action"})),
    }
}

#[derive(Deserialize)]
struct InternalSig {
    peer: String,
    content: String,
}

async fn internal_signaling(State(state): State<AppState>, Json(body): Json<InternalSig>) -> StatusCode {
    state
        .calls
        .lock()
        .expect("calls")
        .handle_signaling(&body.peer, &body.content);
    StatusCode::OK
}

#[derive(Deserialize)]
struct InternalMedia {
    peer: String,
    data: String,
}

async fn internal_media(State(state): State<AppState>, Json(body): Json<InternalMedia>) -> StatusCode {
    let Ok(bytes) = hex::decode(body.data.trim()) else {
        return StatusCode::BAD_REQUEST;
    };
    if !is_media_packet(&bytes) {
        return StatusCode::BAD_REQUEST;
    }
    let peer = body.peer.clone();
    let (pcm_out, relay) = {
        let mut mgr = state.calls.lock().expect("calls");
        mgr.handle_media(&peer, &bytes);
        let relay = chatxz_protocol::MediaPacket::decode(&bytes).map(|pkt| {
            (
                pkt.kind as u8,
                pkt.flags,
                pkt.sequence,
                pkt.timestamp_ms,
                pkt.payload,
            )
        });
        let pcm = mgr
            .with_active_media(|m| m.pop_audio_opus(now_ms()))
            .flatten()
            .and_then(|opus| {
                mgr.with_active_media(|m| m.decode_audio_opus(&opus))
                    .and_then(|r| r.ok())
            });
        (pcm, relay)
    };
    if let Some(pcm) = pcm_out {
        let _ = broadcast_media(&state, &peer, MediaKind::Audio as u8, 0, 0, 0, &pcm).await;
    } else if let Some((kind, flags, seq, ts, payload)) = relay {
        if kind != MediaKind::Audio as u8 {
            let _ = broadcast_media(&state, &peer, kind, flags, seq, ts, &payload).await;
        }
    }
    StatusCode::OK
}

fn now_ms() -> u32 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u32)
        .unwrap_or(0)
}

async fn broadcast_media(
    state: &AppState,
    peer: &str,
    kind: u8,
    flags: u8,
    seq: u32,
    ts: u32,
    payload: &[u8],
) -> Result<(), ()> {
    let msg = json!({
        "type": "media",
        "peer": peer,
        "kind": kind,
        "flags": flags,
        "seq": seq,
        "ts": ts,
        "data": hex::encode(payload),
    });
    // Media WS clients receive via separate task — simplified: stored in global for now
    let _ = state.call_events.send(msg.to_string());
    Ok(())
}

#[derive(Deserialize)]
struct MediaQuery {
    peer: Option<String>,
}

async fn media_ws(
    ws: WebSocketUpgrade,
    State(state): State<AppState>,
    Query(q): Query<MediaQuery>,
) -> Response {
    let peer_filter = q.peer.unwrap_or_default();
    ws.on_upgrade(move |socket| handle_media_ws(socket, state, peer_filter))
}

async fn handle_media_ws(mut socket: WebSocket, state: AppState, peer_filter: String) {
    let id = {
        let mut n = state.media_next_id.lock().expect("id");
        *n += 1;
        *n
    };
    if !peer_filter.is_empty() {
        state
            .media_sockets
            .lock()
            .expect("media")
            .insert(id, peer_filter.clone());
    }

    let mut events = state.call_events.subscribe();

    loop {
        tokio::select! {
            incoming = socket.recv() => {
                match incoming {
                    Some(Ok(Message::Binary(raw))) => {
                        process_media_binary(&state, &peer_filter, &raw).await;
                    }
                    Some(Ok(Message::Text(text))) => {
                        if text.contains("\"ping\"") {
                            let _ = socket.send(Message::Text(r#"{"type":"pong"}"#.into())).await;
                        }
                    }
                    Some(Ok(Message::Close(_))) | None => break,
                    Some(Err(e)) => { warn!(%e, "media ws error"); break }
                    _ => {}
                }
            }
            evt = events.recv() => {
                if let Ok(payload) = evt {
                    if payload.contains("\"type\":\"media\"") {
                        let _ = socket.send(Message::Text(payload.into())).await;
                    }
                }
            }
        }
    }
    state.media_sockets.lock().expect("media").remove(&id);
}

async fn process_media_binary(state: &AppState, peer_filter: &str, raw: &[u8]) {
    if raw.len() < 5 {
        return;
    }
    let frame_type = raw[0];
    let ts = u32::from_be_bytes([raw[1], raw[2], raw[3], raw[4]]);
    let mut mgr = state.calls.lock().expect("calls");
    let active_peer = mgr.active_view().map(|v| v.peer);
    let Some(peer) = active_peer else { return };
    if !peer_filter.is_empty() && peer_filter != peer {
        return;
    }
    let packets: Option<Vec<Vec<u8>>> = match frame_type {
        1 if raw.len() >= 5 + FRAME_BYTES => mgr.with_active_media(|media| {
            let pcm = &raw[5..5 + FRAME_BYTES];
            let opus = media.encode_audio_pcm(pcm).ok()?;
            Some(media.packetize_audio(&opus, ts))
        }).flatten(),
        2 => mgr.with_active_media(|media| {
            let keyframe = raw.get(5).copied().unwrap_or(0) == 1;
            let data = raw.get(6..).unwrap_or(&[]);
            Some(media.packetize_video(data, ts, keyframe))
        }).flatten(),
        3 => mgr.with_active_media(|media| {
            let keyframe = raw.get(5).copied().unwrap_or(0) == 1;
            let data = raw.get(6..).unwrap_or(&[]);
            Some(media.packetize_screen(data, ts, keyframe))
        }).flatten(),
        _ => None,
    };
    if let Some(pkts) = packets {
        mgr.send_media_packets(&peer, pkts);
    }
}