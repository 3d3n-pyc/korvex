//! Prototype of the built-in HTTP/1.1 server (0.5, step 1).
//!
//! Reads each request in full in Rust, then calls a Python `dispatch`
//! function once, without ASGI in between. Its only job for now is to
//! measure how much faster this path is than uvicorn; workers, limits,
//! streaming and real async I/O come later if the numbers justify it.

use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::Arc;

use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::body::Incoming;
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::{Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList};
use tokio::net::TcpListener;

/// `(status, headers, body)`, as returned by the Python dispatch function.
type Reply = (u16, Vec<(Vec<u8>, Vec<u8>)>, Vec<u8>);

/// Serves HTTP on `host:port` until Ctrl+C. Each request is passed to
/// `dispatch(method, path, query_string, headers, body)`, which returns
/// `(status, headers, body)`.
#[pyfunction]
pub fn serve(py: Python<'_>, dispatch: Py<PyAny>, host: &str, port: u16) -> PyResult<()> {
    let addr: SocketAddr = format!("{host}:{port}")
        .parse()
        .map_err(|e| PyValueError::new_err(format!("invalid address: {e}")))?;
    let dispatch = Arc::new(dispatch);
    py.detach(move || run(addr, &dispatch))
        .map_err(|e| PyOSError::new_err(e.to_string()))
}

fn run(addr: SocketAddr, dispatch: &Arc<Py<PyAny>>) -> std::io::Result<()> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let listener = TcpListener::bind(addr).await?;
        loop {
            tokio::select! {
                accepted = listener.accept() => {
                    let (stream, _) = accepted?;
                    stream.set_nodelay(true)?;
                    let dispatch = Arc::clone(dispatch);
                    tokio::spawn(async move {
                        let service = service_fn(move |request| handle(request, Arc::clone(&dispatch)));
                        // A client closing its connection early isn't an error worth reporting.
                        let _ = http1::Builder::new().serve_connection(TokioIo::new(stream), service).await;
                    });
                }
                _ = tokio::signal::ctrl_c() => return Ok(()),
            }
        }
    })
}

async fn handle(
    request: Request<Incoming>,
    dispatch: Arc<Py<PyAny>>,
) -> Result<Response<Full<Bytes>>, Infallible> {
    let (parts, body) = request.into_parts();
    let body = body
        .collect()
        .await
        .map(http_body_util::Collected::to_bytes)
        .unwrap_or_default();
    let reply = Python::attach(|py| -> PyResult<Reply> {
        let headers = PyList::empty(py);
        for (name, value) in &parts.headers {
            headers.append((
                PyBytes::new(py, name.as_str().as_bytes()),
                PyBytes::new(py, value.as_bytes()),
            ))?;
        }
        let query = parts.uri.query().unwrap_or("").as_bytes();
        let args = (
            parts.method.as_str(),
            parts.uri.path(),
            PyBytes::new(py, query),
            headers,
            PyBytes::new(py, &body),
        );
        dispatch.call1(py, args)?.extract(py)
    });
    Ok(match reply {
        Ok((status, headers, body)) => build(status, &headers, body),
        Err(err) => {
            Python::attach(|py| err.print(py));
            build(500, &[], b"Internal Server Error".to_vec())
        }
    })
}

fn build(status: u16, headers: &[(Vec<u8>, Vec<u8>)], body: Vec<u8>) -> Response<Full<Bytes>> {
    let mut builder = Response::builder().status(status);
    for (name, value) in headers {
        builder = builder.header(name.as_slice(), value.as_slice());
    }
    builder
        .body(Full::new(Bytes::from(body)))
        .unwrap_or_else(|_| {
            let mut response =
                Response::new(Full::new(Bytes::from_static(b"Internal Server Error")));
            *response.status_mut() = StatusCode::INTERNAL_SERVER_ERROR;
            response
        })
}
