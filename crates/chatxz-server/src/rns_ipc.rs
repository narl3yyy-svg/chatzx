//! Line-delimited JSON IPC to the headless Python RNS transport daemon.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use base64::{engine::general_purpose::STANDARD as B64, Engine};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;
use tokio::sync::{broadcast, oneshot, Mutex};
use tracing::{info, warn};

static REQ_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Clone)]
pub struct RnsIpc {
    inner: Arc<Mutex<IpcInner>>,
    events: broadcast::Sender<String>,
}

struct IpcInner {
    writer: Option<tokio::io::WriteHalf<TcpStream>>,
    pending: HashMap<u64, oneshot::Sender<IpcHttpResponse>>,
}

#[derive(Debug, Deserialize)]
struct IpcLine {
    op: String,
    id: Option<u64>,
    status: Option<u16>,
    content_type: Option<String>,
    body: Option<Value>,
    body_b64: Option<String>,
    payload: Option<Value>,
    result: Option<Value>,
}

#[derive(Debug)]
pub struct IpcHttpResponse {
    pub status: u16,
    pub content_type: String,
    pub body: Vec<u8>,
}

impl RnsIpc {
    pub async fn connect(addr: &str) -> Result<Self, String> {
        let stream = TcpStream::connect(addr)
            .await
            .map_err(|e| format!("IPC connect {addr}: {e}"))?;
        stream
            .set_nodelay(true)
            .map_err(|e| format!("IPC nodelay: {e}"))?;
        let (reader, writer) = tokio::io::split(stream);
        let (events, _) = broadcast::channel(256);
        let ipc = Self {
            inner: Arc::new(Mutex::new(IpcInner {
                writer: Some(writer),
                pending: HashMap::new(),
            })),
            events: events.clone(),
        };
        tokio::spawn(ipc.clone().read_loop(reader));
        let _ = ipc.ping().await;
        info!(%addr, "RNS IPC connected");
        Ok(ipc)
    }

    pub fn subscribe_events(&self) -> broadcast::Receiver<String> {
        self.events.subscribe()
    }

    async fn read_loop(self, reader: tokio::io::ReadHalf<TcpStream>) {
        let mut lines = BufReader::new(reader).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            let Ok(msg) = serde_json::from_str::<IpcLine>(&line) else {
                continue;
            };
            match msg.op.as_str() {
                "http_response" => {
                    let Some(id) = msg.id else { continue };
                    let content_type = msg
                        .content_type
                        .clone()
                        .unwrap_or_else(|| "application/json".into());
                    let resp = IpcHttpResponse {
                        status: msg.status.unwrap_or(500),
                        content_type,
                        body: encode_body(&msg),
                    };
                    if let Some(tx) = self.inner.lock().await.pending.remove(&id) {
                        let _ = tx.send(resp);
                    }
                }
                "event" => {
                    if let Some(payload) = msg.payload {
                        if let Ok(s) = serde_json::to_string(&payload) {
                            let _ = self.events.send(s);
                        }
                    }
                }
                "rns_response" => {
                    let Some(id) = msg.id else { continue };
                    let body = serde_json::to_vec(&msg.result.unwrap_or(Value::Null))
                        .unwrap_or_default();
                    let resp = IpcHttpResponse {
                        status: 200,
                        content_type: "application/json".into(),
                        body,
                    };
                    if let Some(tx) = self.inner.lock().await.pending.remove(&id) {
                        let _ = tx.send(resp);
                    }
                }
                _ => {}
            }
        }
        warn!("RNS IPC read loop ended");
    }

    async fn send_line(&self, value: Value) -> Result<(), String> {
        let line = serde_json::to_string(&value).map_err(|e| e.to_string())?;
        let mut guard = self.inner.lock().await;
        let writer = guard
            .writer
            .as_mut()
            .ok_or_else(|| "IPC disconnected".to_string())?;
        writer
            .write_all(line.as_bytes())
            .await
            .map_err(|e| e.to_string())?;
        writer.write_all(b"\n").await.map_err(|e| e.to_string())?;
        writer.flush().await.map_err(|e| e.to_string())?;
        Ok(())
    }

    pub async fn ping(&self) -> Result<(), String> {
        self.send_line(json!({"op": "ping"})).await
    }

    pub async fn http_request(
        &self,
        method: &str,
        path: &str,
        query: HashMap<String, String>,
        headers: HashMap<String, String>,
        body: Option<Vec<u8>>,
    ) -> Result<IpcHttpResponse, String> {
        let id = REQ_ID.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = oneshot::channel();
        self.inner.lock().await.pending.insert(id, tx);
        let mut msg = json!({
            "op": "http",
            "id": id,
            "method": method,
            "path": path,
            "query": query,
            "headers": headers,
        });
        if let Some(b) = body {
            msg["body_b64"] = json!(B64.encode(b));
        }
        if let Err(e) = self.send_line(msg).await {
            self.inner.lock().await.pending.remove(&id);
            return Err(e);
        }
        match tokio::time::timeout(std::time::Duration::from_secs(120), rx).await {
            Ok(Ok(resp)) => Ok(resp),
            Ok(Err(_)) => Err("IPC response channel closed".into()),
            Err(_) => {
                self.inner.lock().await.pending.remove(&id);
                Err("IPC request timeout".into())
            }
        }
    }

    pub async fn ws_send(&self, data: Value) -> Result<(), String> {
        self.send_line(json!({"op": "ws", "data": data})).await
    }

    pub async fn rns_call(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = REQ_ID.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = oneshot::channel();
        self.inner.lock().await.pending.insert(id, tx);
        if let Err(e) = self
            .send_line(json!({
                "op": "rns",
                "id": id,
                "method": method,
                "params": params,
            }))
            .await
        {
            self.inner.lock().await.pending.remove(&id);
            return Err(e);
        }
        match tokio::time::timeout(std::time::Duration::from_secs(30), rx).await {
            Ok(Ok(resp)) => serde_json::from_slice(&resp.body).map_err(|e| e.to_string()),
            Ok(Err(_)) => Err("IPC rns channel closed".into()),
            Err(_) => {
                self.inner.lock().await.pending.remove(&id);
                Err("IPC rns timeout".into())
            }
        }
    }

    pub async fn send_signaling(&self, peer: &str, payload: &str) -> Result<(), String> {
        self.rns_call(
            "send_signaling",
            json!({"peer": peer, "payload": payload}),
        )
        .await?;
        Ok(())
    }

    pub async fn send_media(&self, peer: &str, data: &[u8]) -> Result<(), String> {
        self.rns_call(
            "send_media",
            json!({"peer": peer, "data": hex::encode(data)}),
        )
        .await?;
        Ok(())
    }

    pub async fn peer_linked(&self, peer: &str) -> Result<bool, String> {
        let resp = self
            .rns_call("peer_linked", json!({"peer": peer}))
            .await?;
        Ok(resp.get("linked").and_then(|v| v.as_bool()).unwrap_or(false))
    }

    pub async fn any_linked(&self) -> Result<bool, String> {
        let resp = self.rns_call("peer_linked", json!({})).await?;
        Ok(resp.get("linked").and_then(|v| v.as_bool()).unwrap_or(false))
    }
}

fn encode_body(msg: &IpcLine) -> Vec<u8> {
    if let Some(b64) = &msg.body_b64 {
        return B64.decode(b64).unwrap_or_default();
    }
    if let Some(body) = &msg.body {
        if body.is_string() {
            return body.as_str().unwrap_or("").as_bytes().to_vec();
        }
        return serde_json::to_vec(body).unwrap_or_default();
    }
    Vec::new()
}