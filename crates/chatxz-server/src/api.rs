//! HTTP API — served by Rust, delegated to RNS IPC where needed.

use std::collections::HashMap;

use axum::body::Body;
use axum::http::{HeaderMap, Method, StatusCode, Uri};
use axum::response::Response;
use http_body_util::BodyExt;
use tracing::warn;

use crate::rns_ipc::RnsIpc;

pub async fn forward_api(
    ipc: &RnsIpc,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Result<Response, StatusCode> {
    let path = uri.path().to_string();
    let query: HashMap<String, String> = uri
        .query()
        .map(|q| {
            url::form_urlencoded::parse(q.as_bytes())
                .map(|(k, v)| (k.into_owned(), v.into_owned()))
                .collect()
        })
        .unwrap_or_default();

    const HOP: &[&str] = &[
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
    ];
    let mut fwd_headers = HashMap::new();
    for (k, v) in headers.iter() {
        let name = k.as_str().to_lowercase();
        if HOP.contains(&name.as_str()) {
            continue;
        }
        if let Ok(s) = v.to_str() {
            fwd_headers.insert(k.to_string(), s.to_string());
        }
    }

    let body_bytes = body
        .collect()
        .await
        .map_err(|_| StatusCode::BAD_GATEWAY)?
        .to_bytes();
    let body_vec = if body_bytes.is_empty() {
        None
    } else {
        Some(body_bytes.to_vec())
    };

    let resp = ipc
        .http_request(method.as_str(), &path, query, fwd_headers, body_vec)
        .await
        .map_err(|e| {
            warn!(%e, %path, "IPC API request failed");
            StatusCode::BAD_GATEWAY
        })?;

    Response::builder()
        .status(resp.status)
        .header("content-type", &resp.content_type)
        .body(Body::from(resp.body))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}