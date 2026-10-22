//! `dw-serve` — the HTTP adapter for [`dw_api`].
//!
//! Transport only: parse a request, call `dw_api::handle`, write a response.
//! All product behaviour lives in the library. Dependency-free (`std` only)
//! on purpose — the wire shape is the deliverable, and an HTTP framework is
//! not part of the trust story.
//!
//! ```text
//! DW_API_TOKEN=crk_dev GET/POST http://127.0.0.1:8787/…
//! ```
//!
//! Also serves evidence pixels from `DW_EVIDENCE_ROOT` at
//! `/evidence/{pages,crops}/{sha}.png` — assets, not data calls, so the D7
//! "no second call" rule holds for the experience itself.
//!
//! # Lints
//!
//! A `main` that binds, accepts, and maps errors to exit codes is one linear
//! sequence; splitting it would scatter the exit-code contract. `expect_used`
//! is allowed here for the parse helpers whose inputs are length-checked
//! above them.

#![forbid(unsafe_code)]
#![deny(missing_docs)]
#![allow(
    clippy::expect_used,
    reason = "length-checked parse helpers; see module docs"
)]

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};

use std::sync::Arc;

use dw_api::{handle, Request};
use dw_api::{ApiStore, DemoStore, ExportStore, ReleaseStore};

const DEFAULT_ADDR: &str = "127.0.0.1:8787";

fn main() {
    let addr = std::env::var("DW_SERVE_ADDR").unwrap_or_else(|_| DEFAULT_ADDR.to_string());
    let token = std::env::var("DW_API_TOKEN").unwrap_or_else(|_| "crk_dev".to_string());
    let evidence_root: Option<String> = std::env::var("DW_EVIDENCE_ROOT").ok();
    // DW_EXPORT_ROOT slots dw-extract's api-store-export behind ApiStore;
    // without it the demo substrate serves (same wire, demo data).
    let store: Arc<dyn ApiStore + Send + Sync> = if let Ok(root) = std::env::var("DW_RELEASE_ROOT") {
        match ReleaseStore::open_with_evidence(&root, evidence_root.clone().map(std::path::PathBuf::from)) {
            Ok(s) => {
                eprintln!("dw-serve: release store at {root} ({})", s.release_id());
                Arc::new(s)
            }
            Err(e) => {
                // Fail-closed: a release that cannot verify does not serve.
                eprintln!("dw-serve: {e}");
                std::process::exit(2);
            }
        }
    } else {
        match std::env::var("DW_EXPORT_ROOT") {
            Ok(root) => {
                eprintln!("dw-serve: export store at {root}");
                Arc::new(ExportStore::open(root))
            }
            Err(_) => Arc::new(DemoStore::new()),
        }
    };
    eprintln!(
        "dw-serve on {addr} · release {} · token {}",
        store.release_id(),
        if token == "crk_dev" {
            "crk_dev (dev default)"
        } else {
            "from env"
        }
    );
    let listener = match TcpListener::bind(&addr) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("dw-serve: cannot bind {addr}: {e}");
            std::process::exit(1);
        }
    };
    for stream in listener.incoming() {
        match stream {
            Ok(s) => {
                let token = token.clone();
                let evidence_root = evidence_root.clone();
                let store = Arc::clone(&store);
                std::thread::spawn(move || {
                    let _ = serve_conn(s, &token, evidence_root.as_deref(), &*store);
                });
            }
            Err(e) => eprintln!("dw-serve: accept failed: {e}"),
        }
    }
}

/// One connection: one request, one response, close.
fn serve_conn(
    mut stream: TcpStream,
    token: &str,
    evidence_root: Option<&str>,
    store: &dyn ApiStore,
) -> std::io::Result<()> {
    let raw = read_head(&mut stream)?;
    let (head, mut body) = split_head(&raw);
    let (method, path, content_len) = parse_head(&head);
    // The body may already have arrived with the headers — `read_exact` would
    // then wait forever for bytes already consumed. Finish from the remainder.
    if body.len() < content_len {
        let mut rest = vec![0u8; content_len - body.len()];
        stream.read_exact(&mut rest)?;
        body.extend_from_slice(&rest);
    } else {
        body.truncate(content_len);
    }

    // Evidence pixels: assets, served before auth so a renderer can <img>
    // them without a bearer header. They are content-addressed.
    if method == "GET" && path.starts_with("/evidence/") {
        return write_evidence(&mut stream, evidence_root, &path);
    }

    if !authorized(&head, token) {
        let problem = dw_api::Problem::unauthorized(path.clone());
        return write_body(
            &mut stream,
            401,
            "application/problem+json",
            &problem.to_json(),
        );
    }

    let body_value = if body.is_empty() {
        None
    } else {
        match serde_json::from_slice(&body) {
            Ok(v) => Some(v),
            Err(e) => {
                let problem =
                    dw_api::Problem::bad_request(path.clone(), format!("body is not JSON: {e}"));
                return write_body(
                    &mut stream,
                    400,
                    "application/problem+json",
                    &problem.to_json(),
                );
            }
        }
    };

    let request = Request {
        method: method.clone(),
        path: path.clone(),
        body: body_value,
    };
    match handle(&request, store) {
        Ok(response) => write_body(&mut stream, 200, "application/json", &response.to_json()),
        Err(problem) => write_body(
            &mut stream,
            problem.status,
            "application/problem+json",
            &problem.to_json(),
        ),
    }
}

/// Read until the header terminator. Bounded: a hostile peer cannot make us
/// buffer forever. Returns everything read, body remainder included — use
/// [`split_head`] to separate them.
fn read_head(stream: &mut TcpStream) -> std::io::Result<Vec<u8>> {
    let mut buf = Vec::new();
    let mut chunk = [0u8; 1024];
    loop {
        let n = stream.read(&mut chunk)?;
        if n == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "connection closed before headers",
            ));
        }
        if let Some(part) = chunk.get(..n) {
            buf.extend_from_slice(part);
        }
        if buf.windows(4).any(|w| w == b"\r\n\r\n") {
            return Ok(buf);
        }
        if buf.len() > 64 * 1024 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "headers exceed 64 KiB",
            ));
        }
    }
}

/// Split raw request bytes at the header terminator: `(head, body_remainder)`.
fn split_head(raw: &[u8]) -> (Vec<u8>, Vec<u8>) {
    let cut = raw
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .map_or(raw.len(), |i| i + 4);
    (
        raw.get(..cut).unwrap_or_default().to_vec(),
        raw.get(cut..).unwrap_or_default().to_vec(),
    )
}

/// Split the head into method, path, and body length.
fn parse_head(head: &[u8]) -> (String, String, usize) {
    let text = String::from_utf8_lossy(head);
    let mut lines = text.split("\r\n");
    let request_line = lines.next().unwrap_or_default();
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or_default().to_string();
    let path = parts.next().unwrap_or_default().to_string();
    let mut content_len = 0usize;
    for line in lines {
        if let Some(v) = line
            .strip_prefix("Content-Length:")
            .or_else(|| line.strip_prefix("content-length:"))
        {
            content_len = v.trim().parse().unwrap_or(0);
        }
    }
    (method, path, content_len)
}

/// `Authorization: Bearer crk_…` against the configured token.
fn authorized(head: &[u8], token: &str) -> bool {
    let text = String::from_utf8_lossy(head);
    text.lines().any(|l| {
        l.strip_prefix("Authorization:")
            .or_else(|| l.strip_prefix("authorization:"))
            .is_some_and(|v| v.trim() == format!("Bearer {token}"))
    })
}

/// Serve a content-addressed evidence asset, or a renderable 404.
fn write_evidence(
    stream: &mut TcpStream,
    evidence_root: Option<&str>,
    path: &str,
) -> std::io::Result<()> {
    let Some(root) = evidence_root else {
        let problem = dw_api::Problem::new(
            "urn:dw:problem:unavailable",
            "Service unavailable",
            503,
            "no evidence store is mounted on this server",
            path.to_string(),
            "Set DW_EVIDENCE_ROOT to the evidence store (…/m5-opencode/evidence)",
        );
        return write_body(stream, 503, "application/problem+json", &problem.to_json());
    };
    // /evidence/pages/ab/abcd….png or /evidence/pages/abcd….png — accept both
    // the store's sharded layout and a flat one.
    let rel = path.trim_start_matches("/evidence/");
    let rel: Vec<&str> = rel.split('/').filter(|s| !s.is_empty()).collect();
    let (Some(kind), Some(file_name)) = (rel.first().copied(), rel.last().copied()) else {
        let problem = dw_api::Problem::not_found(path.to_string(), "asset", path);
        return write_body(stream, 404, "application/problem+json", &problem.to_json());
    };
    if !matches!(kind, "pages" | "crops") {
        let problem = dw_api::Problem::not_found(path.to_string(), "asset", path);
        return write_body(stream, 404, "application/problem+json", &problem.to_json());
    }
    let prefix = file_name.get(..2).unwrap_or("00").to_string();
    let candidate = std::path::Path::new(root)
        .join(kind)
        .join(prefix)
        .join(file_name);
    if let Ok(bytes) = std::fs::read(&candidate) {
        return write_raw(stream, 200, "image/png", &bytes);
    }
    let problem = dw_api::Problem::not_found(path.to_string(), "asset", path);
    write_body(stream, 404, "application/problem+json", &problem.to_json())
}

/// Write a JSON response.
fn write_body(
    stream: &mut TcpStream,
    status: u16,
    content_type: &str,
    value: &serde_json::Value,
) -> std::io::Result<()> {
    let bytes = serde_json::to_vec(value).unwrap_or_default();
    write_raw(stream, status, content_type, &bytes)
}

/// Write raw bytes with the minimal headers a renderer needs.
fn write_raw(
    stream: &mut TcpStream,
    status: u16,
    content_type: &str,
    bytes: &[u8],
) -> std::io::Result<()> {
    let head = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: {content_type}\r\nContent-Length: {len}\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n",
        reason = reason_phrase(status),
        len = bytes.len(),
    );
    stream.write_all(head.as_bytes())?;
    stream.write_all(bytes)
}

/// Reason phrases for the statuses this server emits. The wildcard covers
/// `200` and any status a [`dw_api::Problem`] invents — clients ignore the
/// phrase, and inventing a wrong one is worse than a neutral default.
fn reason_phrase(status: u16) -> &'static str {
    match status {
        400 => "Bad Request",
        401 => "Unauthorized",
        404 => "Not Found",
        405 => "Method Not Allowed",
        501 => "Not Implemented",
        503 => "Service Unavailable",
        _ => "OK",
    }
}
