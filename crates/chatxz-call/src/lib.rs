//! Call signaling and session management (v2 Rust rewrite).

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use chatxz_media::MediaEngine;
use chatxz_protocol::CallSignal;
use serde::{Deserialize, Serialize};
use tracing::{info, warn};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CallState {
    Idle,
    Outgoing,
    Incoming,
    Connecting,
    Active,
    Ended,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CallMode {
    Audio,
    Video,
    Screen,
}

#[derive(Debug, Clone, Serialize)]
pub struct CallView {
    pub call_id: String,
    pub peer: String,
    pub mode: CallMode,
    pub state: CallState,
    pub outgoing: bool,
    pub muted: bool,
    pub video: bool,
    pub screen: bool,
    pub duration_s: u64,
}

pub struct CallSession {
    pub call_id: String,
    pub peer_hash: String,
    pub mode: CallMode,
    pub outgoing: bool,
    pub state: CallState,
    pub muted: bool,
    pub video: bool,
    pub screen: bool,
    pub media: MediaEngine,
    started_at: std::time::Instant,
}

impl CallSession {
    fn to_view(&self) -> CallView {
        let duration_s = if self.state == CallState::Active {
            self.started_at.elapsed().as_secs()
        } else {
            0
        };
        CallView {
            call_id: self.call_id.clone(),
            peer: self.peer_hash.clone(),
            mode: self.mode,
            state: self.state,
            outgoing: self.outgoing,
            muted: self.muted,
            video: self.video,
            screen: self.screen,
            duration_s,
        }
    }
}

pub type EventCallback = Arc<dyn Fn(&str, CallView) + Send + Sync>;
pub type SendSignalingFn = Arc<dyn Fn(&str, &str) + Send + Sync>;
pub type SendMediaFn = Arc<dyn Fn(&str, &[u8]) + Send + Sync>;
pub struct CallManager {
    sessions: HashMap<String, CallSession>,
    active_id: Option<String>,
    send_signaling: SendSignalingFn,
    send_media: SendMediaFn,
    on_event: Option<EventCallback>,
}

impl CallManager {
    pub fn new(send_signaling: SendSignalingFn, send_media: SendMediaFn) -> Self {
        Self {
            sessions: HashMap::new(),
            active_id: None,
            send_signaling,
            send_media,
            on_event: None,
        }
    }

    pub fn set_event_handler(&mut self, cb: EventCallback) {
        self.on_event = Some(cb);
    }

    fn emit(&self, event: &str, session: &CallSession) {
        if let Some(cb) = &self.on_event {
            cb(event, session.to_view());
        }
    }

    fn send_action(&self, peer: &str, action: &str, call_id: &str, extra: CallSignal) {
        let mut sig = extra;
        sig.action = action.into();
        sig.call_id = Some(call_id.into());
        (self.send_signaling)(peer, &sig.to_json());
    }

    pub fn active_view(&self) -> Option<CallView> {
        self.active_id
            .as_ref()
            .and_then(|id| self.sessions.get(id))
            .map(|s| s.to_view())
    }

    pub fn start_call(&mut self, peer_hash: &str, mode: CallMode) -> Option<CallView> {
        if self.active_id.is_some() {
            return None;
        }
        let call_id = Uuid::new_v4().to_string();
        let video = matches!(mode, CallMode::Video | CallMode::Screen);
        let screen = mode == CallMode::Screen;
        let media = MediaEngine::new().ok()?;
        let session = CallSession {
            call_id: call_id.clone(),
            peer_hash: peer_hash.to_string(),
            mode,
            outgoing: true,
            state: CallState::Outgoing,
            muted: false,
            video,
            screen,
            media,
            started_at: std::time::Instant::now(),
        };
        self.send_action(
            peer_hash,
            "invite",
            &call_id,
            CallSignal {
                action: "invite".into(),
                call_id: Some(call_id.clone()),
                mode: Some(mode_to_str(mode).into()),
                muted: None,
                video: Some(video),
                screen: Some(screen),
                stats: None,
            },
        );
        self.sessions.insert(call_id.clone(), session);
        self.active_id = Some(call_id.clone());
        let view = self.sessions.get(&call_id)?.to_view();
        self.emit("outgoing", self.sessions.get(&call_id)?);
        Some(view)
    }

    pub fn accept_call(&mut self, call_id: &str) -> bool {
        let id = if call_id.is_empty() {
            self.active_id.clone().unwrap_or_default()
        } else {
            call_id.to_string()
        };
        let Some(session) = self.sessions.get_mut(&id) else {
            return false;
        };
        if session.state != CallState::Incoming {
            return false;
        }
        let peer = session.peer_hash.clone();
        let cid = session.call_id.clone();
        session.state = CallState::Active;
        session.started_at = std::time::Instant::now();
        self.send_action(
            &peer,
            "accept",
            &cid,
            CallSignal {
                action: "accept".into(),
                call_id: Some(cid.clone()),
                mode: None,
                muted: None,
                video: None,
                screen: None,
                stats: None,
            },
        );
        self.emit("active", self.sessions.get(&cid).unwrap());
        true
    }

    pub fn reject_call(&mut self, call_id: &str) {
        let id = if call_id.is_empty() {
            self.active_id.clone().unwrap_or_default()
        } else {
            call_id.to_string()
        };
        if let Some(session) = self.sessions.get(&id) {
            let peer = session.peer_hash.clone();
            let cid = session.call_id.clone();
            self.send_action(
                &peer,
                "reject",
                &cid,
                CallSignal {
                    action: "reject".into(),
                    call_id: Some(cid.clone()),
                    mode: None,
                    muted: None,
                    video: None,
                    screen: None,
                    stats: None,
                },
            );
        }
        self.end_session(&id, false);
    }

    pub fn hangup(&mut self, call_id: Option<&str>) {
        let id = call_id
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .or_else(|| self.active_id.clone())
            .unwrap_or_default();
        if id.is_empty() {
            return;
        }
        if let Some(session) = self.sessions.get(&id) {
            if session.state != CallState::Idle && session.state != CallState::Ended {
                let peer = session.peer_hash.clone();
                let cid = session.call_id.clone();
                self.send_action(
                    &peer,
                    "hangup",
                    &cid,
                    CallSignal {
                        action: "hangup".into(),
                        call_id: Some(cid.clone()),
                        mode: None,
                        muted: None,
                        video: None,
                        screen: None,
                        stats: None,
                    },
                );
            }
        }
        self.end_session(&id, true);
    }

    fn end_session(&mut self, call_id: &str, notify: bool) {
        if let Some(mut session) = self.sessions.remove(call_id) {
            session.state = CallState::Ended;
            session.media.reset();
            if notify {
                self.emit("ended", &session);
            }
        }
        if self.active_id.as_deref() == Some(call_id) {
            self.active_id = None;
        }
    }

    pub fn handle_signaling(&mut self, peer_hash: &str, content: &str) {
        let Some(sig) = CallSignal::from_json(content) else {
            warn!(peer = peer_hash, "invalid call signaling JSON");
            return;
        };
        let action = sig.action.as_str();
        let call_id = sig.call_id.clone().unwrap_or_default();

        match action {
            "invite" => {
                if self.active_id.is_some() {
                    self.send_action(
                        peer_hash,
                        "busy",
                        &call_id,
                        CallSignal {
                            action: "busy".into(),
                            call_id: Some(call_id.clone()),
                            mode: None,
                            muted: None,
                            video: None,
                            screen: None,
                            stats: None,
                        },
                    );
                    return;
                }
                let mode = parse_mode(sig.mode.as_deref());
                let media = match MediaEngine::new() {
                    Ok(m) => m,
                    Err(e) => {
                        warn!(%e, "media engine init failed");
                        return;
                    }
                };
                let session = CallSession {
                    call_id: call_id.clone(),
                    peer_hash: peer_hash.to_string(),
                    mode,
                    outgoing: false,
                    state: CallState::Incoming,
                    muted: false,
                    video: sig.video.unwrap_or(mode != CallMode::Audio),
                    screen: sig.screen.unwrap_or(mode == CallMode::Screen),
                    media,
                    started_at: std::time::Instant::now(),
                };
                self.sessions.insert(call_id.clone(), session);
                self.active_id = Some(call_id.clone());
                self.emit("incoming", self.sessions.get(&call_id).unwrap());
            }
            "accept" => {
                if let Some(session) = self.sessions.get_mut(&call_id) {
                    if session.outgoing && session.state == CallState::Outgoing {
                        session.state = CallState::Active;
                        session.started_at = std::time::Instant::now();
                        self.emit("active", self.sessions.get(&call_id).unwrap());
                    }
                }
            }
            "reject" | "busy" => {
                self.end_session(&call_id, true);
            }
            "hangup" => {
                self.end_session(&call_id, true);
            }
            "update" => {
                if let Some(session) = self.sessions.get_mut(&call_id) {
                    if let Some(m) = sig.muted {
                        session.muted = m;
                    }
                    if let Some(v) = sig.video {
                        session.video = v;
                    }
                    if let Some(s) = sig.screen {
                        session.screen = s;
                    }
                }
            }
            _ => info!(action, "ignored call action"),
        }
    }

    pub fn handle_media(&mut self, peer_hash: &str, data: &[u8]) {
        let active = self.active_id.clone();
        let Some(id) = active else { return };
        let Some(session) = self.sessions.get_mut(&id) else {
            return;
        };
        if session.peer_hash != peer_hash {
            return;
        };
        session.media.ingest(data);
    }

    pub fn send_media_packets(&self, peer_hash: &str, packets: Vec<Vec<u8>>) {
        for pkt in packets {
            if chatxz_protocol::MediaPacket::fits_mtu(&pkt) {
                (self.send_media)(peer_hash, &pkt);
            }
        }
    }

    pub fn with_active_media<F, R>(&mut self, f: F) -> Option<R>
    where
        F: FnOnce(&mut MediaEngine) -> R,
    {
        let id = self.active_id.clone()?;
        let session = self.sessions.get_mut(&id)?;
        Some(f(&mut session.media))
    }

    pub fn update_call(
        &mut self,
        muted: Option<bool>,
        video: Option<bool>,
        screen: Option<bool>,
    ) -> Option<CallView> {
        let id = self.active_id.clone()?;
        let session = self.sessions.get_mut(&id)?;
        if let Some(m) = muted {
            session.muted = m;
        }
        if let Some(v) = video {
            session.video = v;
        }
        if let Some(s) = screen {
            session.screen = s;
        }
        Some(session.to_view())
    }
}

fn mode_to_str(mode: CallMode) -> &'static str {
    match mode {
        CallMode::Audio => "audio",
        CallMode::Video => "video",
        CallMode::Screen => "screen",
    }
}

fn parse_mode(raw: Option<&str>) -> CallMode {
    match raw.unwrap_or("audio").to_lowercase().as_str() {
        "video" => CallMode::Video,
        "screen" => CallMode::Screen,
        _ => CallMode::Audio,
    }
}

/// Thread-safe wrapper for the HTTP server.
pub type SharedCallManager = Arc<Mutex<CallManager>>;