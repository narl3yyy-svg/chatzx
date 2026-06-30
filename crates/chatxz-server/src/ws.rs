//! UI WebSocket — native Rust endpoint, events from RNS IPC.

use axum::extract::ws::{Message, WebSocket};
use serde_json::Value;
use tracing::warn;

use crate::rns_ipc::RnsIpc;

pub async fn handle_ui_ws(
    mut socket: WebSocket,
    ipc: RnsIpc,
    mut call_events: tokio::sync::broadcast::Receiver<String>,
) {
    let mut ipc_events = ipc.subscribe_events();
    loop {
        tokio::select! {
            incoming = socket.recv() => {
                match incoming {
                    Some(Ok(Message::Text(text))) => {
                        if let Ok(data) = serde_json::from_str::<Value>(&text) {
                            if let Err(e) = ipc.ws_send(data).await {
                                warn!(%e, "IPC ws forward failed");
                            }
                        }
                    }
                    Some(Ok(Message::Binary(data))) => {
                        if let Ok(data) = serde_json::from_slice::<Value>(&data) {
                            let _ = ipc.ws_send(data).await;
                        }
                    }
                    Some(Ok(Message::Ping(data))) => {
                        let _ = socket.send(Message::Pong(data)).await;
                    }
                    Some(Ok(Message::Close(_))) | None => break,
                    Some(Err(e)) => { warn!(%e, "ui ws error"); break }
                    _ => {}
                }
            }
            evt = call_events.recv() => {
                if let Ok(payload) = evt {
                    if socket.send(Message::Text(payload.into())).await.is_err() {
                        break;
                    }
                }
            }
            evt = ipc_events.recv() => {
                if let Ok(payload) = evt {
                    if socket.send(Message::Text(payload.into())).await.is_err() {
                        break;
                    }
                }
            }
        }
    }
}