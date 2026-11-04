//! The built-in HTTP/1.1 server.
//!
//! Two threads share the work:
//! - a Rust thread (tokio, hyper) accepts connections and reads each
//!   request in full, without touching Python;
//! - Python's main thread runs the asyncio loop and the handlers.
//!
//! The Rust thread queues each request and wakes the loop by writing one
//! byte to a socket the loop watches; one wake-up covers every request
//! queued meanwhile. Python takes the queued requests, runs them, and
//! answers each through its `Responder`, which hands the response back to
//! the Rust thread over a channel.

use std::collections::VecDeque;
use std::convert::Infallible;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener as StdTcpListener};
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, PoisonError};
use std::task::{Context, Poll};
use std::thread::JoinHandle;

use bytes::Bytes;
use http_body_util::{BodyExt, Either, Full};
use hyper::body::{Body, Frame, Incoming};
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::{Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use pyo3::exceptions::{PyOSError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList};
use tokio::net::TcpListener;
use tokio::sync::{Notify, mpsc, oneshot};

type Headers = Vec<(Vec<u8>, Vec<u8>)>;
type ResponseBody = Either<Full<Bytes>, ChannelBody>;

/// A request read by the Rust thread, waiting for Python.
struct Pending {
    method: String,
    path: String,
    query: Vec<u8>,
    headers: Headers,
    body: Bytes,
    reply: oneshot::Sender<Reply>,
}

/// What Python answers: a status, headers, and a body, whole or streamed.
struct Reply {
    status: u16,
    headers: Headers,
    body: ResponseBody,
}

/// State shared by the Rust thread and Python.
struct Shared {
    queue: Mutex<VecDeque<Pending>>,
    /// Set when a wake-up byte is on its way and the loop hasn't taken the
    /// queue yet, so a burst of requests sends a single byte.
    woken: AtomicBool,
    wake_writer: UnixStream,
}

impl Shared {
    fn push(&self, pending: Pending) {
        self.queue
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .push_back(pending);
        if !self.woken.swap(true, Ordering::AcqRel) {
            // If the socket buffer is full, bytes are already waiting: the loop will wake anyway.
            let _ = (&self.wake_writer).write(&[1]);
        }
    }
}

/// The server, as Python sees it: `Server(host, port)` starts accepting
/// connections in a Rust thread; the asyncio loop watches `fileno()` and
/// calls `take()` when it becomes readable.
#[pyclass(module = "korvex._core")]
pub struct Server {
    shared: Arc<Shared>,
    wake_reader: UnixStream,
    stop: Arc<Notify>,
    thread: Option<JoinHandle<std::io::Result<()>>>,
}

#[pymethods]
impl Server {
    #[new]
    fn new(host: &str, port: u16) -> PyResult<Self> {
        let addr: SocketAddr = format!("{host}:{port}")
            .parse()
            .map_err(|e| PyValueError::new_err(format!("invalid address {host}:{port}: {e}")))?;
        // Bind here, so a port already in use fails right away in Python.
        let listener = StdTcpListener::bind(addr)
            .map_err(|e| PyOSError::new_err(format!("can't listen on {addr}: {e}")))?;
        listener.set_nonblocking(true)?;
        let (wake_reader, wake_writer) = UnixStream::pair()?;
        wake_reader.set_nonblocking(true)?;
        wake_writer.set_nonblocking(true)?;

        let shared = Arc::new(Shared {
            queue: Mutex::new(VecDeque::new()),
            woken: AtomicBool::new(false),
            wake_writer,
        });
        let stop = Arc::new(Notify::new());
        let thread = {
            let (shared, stop) = (Arc::clone(&shared), Arc::clone(&stop));
            std::thread::Builder::new()
                .name("korvex-server".into())
                .spawn(move || accept_loop(listener, &shared, &stop))?
        };
        Ok(Self {
            shared,
            wake_reader,
            stop,
            thread: Some(thread),
        })
    }

    /// The socket the asyncio loop watches: it becomes readable when
    /// requests are waiting.
    fn fileno(&self) -> i32 {
        self.wake_reader.as_raw_fd()
    }

    /// Every request waiting, as `(method, path, query_string, headers,
    /// body, responder)` tuples.
    fn take<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        // Order matters, or a wake-up gets lost: empty the socket, then clear
        // the flag, then drain the queue. A request queued after the flag is
        // cleared writes a new byte, so the loop wakes again (at worst once
        // for nothing). Clearing the flag first would let this call swallow
        // the byte of a request it then drains, leaving the flag set with no
        // byte on its way: the next request would never wake the loop.
        let mut buffer = [0u8; 256];
        while matches!((&self.wake_reader).read(&mut buffer), Ok(n) if n > 0) {}
        self.shared.woken.store(false, Ordering::Release);

        let pending: Vec<Pending> = self
            .shared
            .queue
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .drain(..)
            .collect();
        let requests = PyList::empty(py);
        for request in pending {
            let headers = PyList::empty(py);
            for (name, value) in &request.headers {
                headers.append((PyBytes::new(py, name), PyBytes::new(py, value)))?;
            }
            requests.append((
                request.method,
                request.path,
                PyBytes::new(py, &request.query),
                headers,
                PyBytes::new(py, &request.body),
                Responder {
                    reply: Mutex::new(Some(request.reply)),
                    stream: Mutex::new(None),
                },
            ))?;
        }
        Ok(requests)
    }

    /// Stops accepting connections and waits for the Rust thread to end.
    fn close(&mut self, py: Python<'_>) -> PyResult<()> {
        self.stop.notify_one();
        if let Some(thread) = self.thread.take() {
            py.detach(|| thread.join())
                .map_err(|_| PyRuntimeError::new_err("the server thread panicked"))?
                .map_err(|e| PyOSError::new_err(e.to_string()))?;
        }
        Ok(())
    }
}

/// How Python answers one request: `respond()` with a whole body, or
/// `start_stream()`, then `send_chunk()` for each piece and `end_stream()`.
/// If Python drops it without answering, the client gets a 500.
#[pyclass(frozen, module = "korvex._core")]
pub struct Responder {
    reply: Mutex<Option<oneshot::Sender<Reply>>>,
    stream: Mutex<Option<mpsc::UnboundedSender<Bytes>>>,
}

impl Responder {
    fn send(&self, status: u16, headers: Headers, body: ResponseBody) {
        if let Some(reply) = self
            .reply
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take()
        {
            // An error means the client is gone: there's no one left to answer.
            let _ = reply.send(Reply {
                status,
                headers,
                body,
            });
        }
    }
}

#[pymethods]
impl Responder {
    fn respond(&self, status: u16, headers: Headers, body: &[u8]) {
        self.send(
            status,
            headers,
            Either::Left(Full::new(Bytes::copy_from_slice(body))),
        );
    }

    fn start_stream(&self, status: u16, headers: Headers) {
        let (sender, receiver) = mpsc::unbounded_channel();
        *self.stream.lock().unwrap_or_else(PoisonError::into_inner) = Some(sender);
        self.send(status, headers, Either::Right(ChannelBody { receiver }));
    }

    /// Sends one piece of a streamed body. Returns `False` once the client
    /// has gone, so Python can stop producing.
    fn send_chunk(&self, chunk: &[u8]) -> bool {
        self.stream
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .as_ref()
            .is_some_and(|sender| sender.send(Bytes::copy_from_slice(chunk)).is_ok())
    }

    fn end_stream(&self) {
        self.stream
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take();
    }
}

/// A response body fed piece by piece by Python; it ends when Python drops
/// its sender (`end_stream()`).
pub struct ChannelBody {
    receiver: mpsc::UnboundedReceiver<Bytes>,
}

impl Body for ChannelBody {
    type Data = Bytes;
    type Error = Infallible;

    fn poll_frame(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Result<Frame<Bytes>, Infallible>>> {
        self.receiver
            .poll_recv(cx)
            .map(|chunk| chunk.map(|bytes| Ok(Frame::data(bytes))))
    }
}

fn accept_loop(
    listener: StdTcpListener,
    shared: &Arc<Shared>,
    stop: &Notify,
) -> std::io::Result<()> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let listener = TcpListener::from_std(listener)?;
        loop {
            tokio::select! {
                accepted = listener.accept() => {
                    // e.g. too many open files: skip this one and keep accepting.
                    let Ok((stream, _)) = accepted else { continue };
                    let _ = stream.set_nodelay(true);
                    let shared = Arc::clone(shared);
                    tokio::spawn(async move {
                        let service = service_fn(move |request| handle(request, Arc::clone(&shared)));
                        // A client closing its connection early isn't an error worth reporting.
                        let _ = http1::Builder::new().serve_connection(TokioIo::new(stream), service).await;
                    });
                }
                () = stop.notified() => return Ok(()),
            }
        }
    })
}

async fn handle(
    request: Request<Incoming>,
    shared: Arc<Shared>,
) -> Result<Response<ResponseBody>, Infallible> {
    let (parts, body) = request.into_parts();
    let body = body
        .collect()
        .await
        .map(http_body_util::Collected::to_bytes)
        .unwrap_or_default();
    let (reply, answer) = oneshot::channel();
    shared.push(Pending {
        method: parts.method.as_str().to_owned(),
        path: parts.uri.path().to_owned(),
        query: parts.uri.query().unwrap_or("").as_bytes().to_vec(),
        headers: parts
            .headers
            .iter()
            .map(|(name, value)| (name.as_str().as_bytes().to_vec(), value.as_bytes().to_vec()))
            .collect(),
        body,
        reply,
    });
    // An error means Python dropped the request without answering (it raised).
    Ok(match answer.await {
        Ok(Reply {
            status,
            headers,
            body,
        }) => build(status, &headers, body),
        Err(_) => internal_error(),
    })
}

fn build(
    status: u16,
    headers: &[(Vec<u8>, Vec<u8>)],
    body: ResponseBody,
) -> Response<ResponseBody> {
    let mut builder = Response::builder().status(status);
    for (name, value) in headers {
        builder = builder.header(name.as_slice(), value.as_slice());
    }
    // Invalid status or header (e.g. a newline in a value): don't send it.
    builder.body(body).unwrap_or_else(|_| internal_error())
}

fn internal_error() -> Response<ResponseBody> {
    let mut response = Response::new(Either::Left(Full::new(Bytes::from_static(
        b"Internal Server Error",
    ))));
    *response.status_mut() = StatusCode::INTERNAL_SERVER_ERROR;
    response
}
