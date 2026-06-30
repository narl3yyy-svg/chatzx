use serde_json::json;

#[derive(Clone)]
pub struct RnsClient {
    backend: String,
    http: reqwest::Client,
}

impl RnsClient {
    pub fn new(backend: &str) -> Self {
        Self {
            backend: backend.trim_end_matches('/').to_string(),
            http: reqwest::Client::new(),
        }
    }

    pub async fn send_signaling(&self, peer: &str, payload: &str) -> Result<(), String> {
        let url = format!("{}/api/internal/rns/signaling", self.backend);
        self.http
            .post(&url)
            .json(&json!({ "peer": peer, "payload": payload }))
            .send()
            .await
            .map_err(|e| e.to_string())?
            .error_for_status()
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub async fn send_media(&self, peer: &str, data: &[u8]) -> Result<(), String> {
        let url = format!("{}/api/internal/rns/media", self.backend);
        self.http
            .post(&url)
            .json(&json!({
                "peer": peer,
                "data": hex::encode(data),
            }))
            .send()
            .await
            .map_err(|e| e.to_string())?
            .error_for_status()
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub async fn peer_linked(&self, peer: &str) -> Result<bool, String> {
        let url = format!("{}/api/internal/rns/linked?peer={}", self.backend, peer);
        let resp: serde_json::Value = self
            .http
            .get(&url)
            .send()
            .await
            .map_err(|e| e.to_string())?
            .json()
            .await
            .map_err(|e| e.to_string())?;
        Ok(resp.get("linked").and_then(|v| v.as_bool()).unwrap_or(false))
    }

    pub async fn any_linked(&self) -> Result<bool, String> {
        let url = format!("{}/api/internal/rns/linked", self.backend);
        let resp: serde_json::Value = self
            .http
            .get(&url)
            .send()
            .await
            .map_err(|e| e.to_string())?
            .json()
            .await
            .map_err(|e| e.to_string())?;
        Ok(resp.get("linked").and_then(|v| v.as_bool()).unwrap_or(false))
    }

    pub async fn post_call_event(
        &self,
        event: &str,
        data: &serde_json::Value,
    ) -> Result<(), String> {
        let url = format!("{}/api/internal/rust/call-event", self.backend);
        self.http
            .post(&url)
            .json(&json!({ "event": event, "data": data }))
            .send()
            .await
            .map_err(|e| e.to_string())?
            .error_for_status()
            .map_err(|e| e.to_string())?;
        Ok(())
    }
}