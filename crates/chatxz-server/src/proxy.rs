use std::path::PathBuf;

use axum::body::Body;
use axum::extract::State;
use axum::http::{Request, StatusCode};
use axum::response::Response;

use crate::AppState;

pub async fn serve_static(
    State(state): State<AppState>,
    req: Request<Body>,
) -> Result<Response, StatusCode> {
    serve_static_file(&state.static_root, req).await
}

async fn serve_static_file(root: &PathBuf, req: Request<Body>) -> Result<Response, StatusCode> {
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
        return Ok(Response::builder()
            .status(StatusCode::OK)
            .header("content-type", mime_guess(path))
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