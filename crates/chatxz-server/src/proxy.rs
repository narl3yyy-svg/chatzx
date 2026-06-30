use std::path::PathBuf;

use axum::body::Body;
use axum::extract::State;
use axum::http::{Request, StatusCode};
use axum::response::Response;
use hyper_util::client::legacy::Client;
use hyper_util::rt::TokioExecutor;
use tracing::warn;

use crate::AppState;

type HttpClient = Client<hyper_util::client::legacy::connect::HttpConnector, Body>;

pub async fn forward(
    State(state): State<AppState>,
    req: Request<Body>,
) -> Result<Response, StatusCode> {
    let path = req.uri().path();

    if path == "/" || path.starts_with("/static/") || path.ends_with(".js")
        || path.ends_with(".css") || path.ends_with(".html")
        || path.ends_with(".json") || path.ends_with(".png") || path.ends_with(".svg")
        || path.ends_with(".ico") || path.ends_with(".woff2")
    {
        return serve_static(&state.static_root, req).await;
    }

    proxy_to_backend(&state.backend, req).await
}

async fn serve_static(root: &PathBuf, req: Request<Body>) -> Result<Response, StatusCode> {
    let path = req.uri().path();
    let file = if path == "/" || path.is_empty() {
        root.join("index.html")
    } else if path.starts_with("/static/") {
        root.parent().unwrap_or(root).join(path.trim_start_matches('/'))
    } else {
        root.join(path.trim_start_matches('/'))
    };
    if file.is_file() {
        let bytes = tokio::fs::read(&file).await.map_err(|_| StatusCode::NOT_FOUND)?;
        let mime = mime_guess(path);
        return Ok(Response::builder()
            .status(StatusCode::OK)
            .header("content-type", mime)
            .body(Body::from(bytes))
            .unwrap());
    }
    let index = root.join("index.html");
    if index.is_file() {
        let bytes = tokio::fs::read(&index).await.map_err(|_| StatusCode::NOT_FOUND)?;
        return Ok(Response::builder()
            .status(StatusCode::OK)
            .header("content-type", "text/html")
            .body(Body::from(bytes))
            .unwrap());
    }
    Err(StatusCode::NOT_FOUND)
}

fn mime_guess(path: &str) -> &'static str {
    if path.ends_with(".js") {
        "application/javascript"
    } else if path.ends_with(".css") {
        "text/css"
    } else if path.ends_with(".json") {
        "application/json"
    } else if path.ends_with(".svg") {
        "image/svg+xml"
    } else if path.ends_with(".png") {
        "image/png"
    } else {
        "text/html"
    }
}

async fn proxy_to_backend(backend: &str, req: Request<Body>) -> Result<Response, StatusCode> {
    let client: HttpClient = Client::builder(TokioExecutor::new()).build_http();
    let uri = format!(
        "{}{}",
        backend.trim_end_matches('/'),
        req.uri().path_and_query().map(|p| p.as_str()).unwrap_or("/")
    );
    let (parts, body) = req.into_parts();
    let body_bytes = axum::body::to_bytes(body, 64 * 1024 * 1024)
        .await
        .map_err(|_| StatusCode::BAD_GATEWAY)?;
    let mut builder = hyper::Request::builder().method(parts.method).uri(&uri);
    for (k, v) in parts.headers.iter() {
        if k != hyper::header::HOST {
            builder = builder.header(k, v);
        }
    }
    let out_req = builder
        .body(Body::from(body_bytes))
        .map_err(|_| StatusCode::BAD_GATEWAY)?;
    let resp = client.request(out_req).await.map_err(|e| {
        warn!(%e, "backend proxy failed");
        StatusCode::BAD_GATEWAY
    })?;
    let (parts, body) = resp.into_parts();
    let bytes = http_body_util::BodyExt::collect(body)
        .await
        .map_err(|_| StatusCode::BAD_GATEWAY)?
        .to_bytes();
    let mut out = Response::builder().status(parts.status);
    for (k, v) in parts.headers.iter() {
        out = out.header(k, v);
    }
    out.body(Body::from(bytes))
        .map_err(|_| StatusCode::BAD_GATEWAY)
}