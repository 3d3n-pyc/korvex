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
use std::os::fd::{AsRawFd, FromRawFd, RawFd};
use std::os::unix::net::{UnixListener as StdUnixListener, UnixStream};
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, PoisonError};
use std::task::{Context, Poll};
use std::thread::JoinHandle;
use std::time::Duration;

use bytes::Bytes;
use http_body_util::{BodyExt, Either, Full, LengthLimitError, Limited};
use hyper::body::{Body, Frame};
use hyper::header::CONTENT_LENGTH;
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::{Request, Response, StatusCode};
use hyper_util::rt::{TokioIo, TokioTimer};
use pyo3::exceptions::{PyOSError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList};
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::net::{TcpListener, UnixListener};
use tokio::sync::{Notify, OwnedSemaphorePermit, Semaphore, mpsc, oneshot, watch};

use crate::method::Method;
use crate::path::Constraints;
use crate::proxy::{Trusted, resolve};
use crate::tree::{Node, Outcome};
use crate::websocket::{self, Incoming, Opening};

pub type Headers = Vec<(Vec<u8>, Vec<u8>)>;
pub type ResponseBody = Either<Full<Bytes>, ChannelBody>;

/// What the Rust thread queues for Python.
pub enum Event {
    Request(Pending),
    /// A WebSocket handshake for the app to accept or refuse.
    WebSocket(Opening),
    /// A message on an open WebSocket, or the news that it closed.
    WebSocketMessage(u64, Incoming),
    /// A streamed response or WebSocket that was asked to wait can take
    /// more: its client caught up (or left).
    Writable(u64),
}

/// How many bytes of a streamed response, or of a WebSocket's outgoing
/// messages, may wait for a slow client before Python is asked to wait.
pub const HIGH_WATER: u64 = 1024 * 1024;
/// Once a waiting backlog is down to this, Python is told to go on.
pub const LOW_WATER: u64 = HIGH_WATER / 4;

/// What Python sends and the client hasn't taken yet, for one streamed
/// response or WebSocket. Python adds to it; the Rust side takes from it
/// as the client reads. Past `HIGH_WATER`, Python waits for a `Writable`
/// event, sent once the backlog is down to `LOW_WATER`: a slow client no
/// longer lets a stream pile up in memory.
pub struct Backlog {
    id: u64,
    bytes: AtomicU64,
    /// Python was told to wait and hasn't been woken yet.
    waiting: AtomicBool,
    shared: Arc<Shared>,
}

/// `send_chunk()` and friends: what Python should do next.
pub const GONE: u8 = 0;
pub const SENT: u8 = 1;
pub const WAIT: u8 = 2;

impl Backlog {
    pub fn new(id: u64, shared: Arc<Shared>) -> Self {
        Self {
            id,
            bytes: AtomicU64::new(0),
            waiting: AtomicBool::new(false),
            shared,
        }
    }

    /// Python queued `n` more bytes: `SENT`, or `WAIT` for a `Writable`.
    pub fn add(&self, n: usize) -> u8 {
        let now = self.bytes.fetch_add(n as u64, Ordering::SeqCst) + n as u64;
        if now <= HIGH_WATER {
            return SENT;
        }
        self.waiting.store(true, Ordering::SeqCst);
        // The client may have caught up in between without seeing `waiting`:
        // then whoever clears the flag decides, so exactly one wake-up (or
        // none at all) happens.
        if self.bytes.load(Ordering::SeqCst) <= LOW_WATER
            && self.waiting.swap(false, Ordering::SeqCst)
        {
            return SENT;
        }
        WAIT
    }

    /// The client took `n` bytes.
    pub fn taken(&self, n: usize) {
        let now = self
            .bytes
            .fetch_sub(n as u64, Ordering::SeqCst)
            .saturating_sub(n as u64);
        if now <= LOW_WATER {
            self.wake();
        }
    }

    /// Wakes Python if it waits: the client caught up, or left.
    pub fn wake(&self) {
        if self.waiting.swap(false, Ordering::SeqCst) {
            self.shared.push(Event::Writable(self.id));
        }
    }
}

/// A request read by the Rust thread, waiting for Python.
pub struct Pending {
    method: String,
    path: String,
    query: Vec<u8>,
    headers: Headers,
    body: Bytes,
    /// `(host, port)`, after `X-Forwarded-For` from a trusted proxy.
    client: Option<(String, u16)>,
    scheme: &'static str,
    /// A status the server already decided on (413, 408, 400), for the
    /// app to answer as an error, without the body; 0 otherwise.
    error: u16,
    reply: oneshot::Sender<Reply>,
}

/// What Python answers: a status, headers, and a body, whole or streamed.
struct Reply {
    status: u16,
    headers: Headers,
    body: ResponseBody,
}

/// State shared by the Rust thread and Python.
pub struct Shared {
    queue: Mutex<VecDeque<Event>>,
    /// Set when a wake-up byte is on its way and the loop hasn't taken the
    /// queue yet, so a burst of requests sends a single byte.
    woken: AtomicBool,
    wake_writer: UnixStream,
    /// How long `stop()` lets open connections finish, in milliseconds.
    grace_ms: AtomicU64,
    /// Set once the server has stopped and every connection is closed.
    finished: AtomicBool,
    /// Largest request body (and WebSocket message) accepted, in bytes,
    /// unless a route sets its own.
    pub max_body: u64,
    /// Per-route body limits, matched like routes: `(method, pattern) -> bytes`.
    body_limits: Node<u64>,
    /// One permit per open connection when the number is limited; `None`
    /// for no limit.
    connections: Option<Arc<Semaphore>>,
    /// How long a client gets to send the headers, then the body.
    timeout: Duration,
    /// How often open WebSocket connections get a ping; `None` for never.
    pub ws_ping_interval: Option<Duration>,
    /// Proxies whose forwarding headers are believed.
    pub trusted: Trusted,
    /// The next ID for a WebSocket or a streamed response.
    pub next_id: AtomicU64,
}

impl Shared {
    /// The body size limit for a request: its route's, or the default.
    fn body_limit(&self, method: &str, path: &str) -> u64 {
        let Some(method) = Method::parse(method) else {
            return self.max_body;
        };
        match self.body_limits.find(path, method, &mut Vec::new()) {
            Outcome::Matched(limit) => *limit,
            _ => self.max_body,
        }
    }
}

impl Shared {
    pub fn push(&self, event: Event) {
        self.queue
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .push_back(event);
        if !self.woken.swap(true, Ordering::AcqRel) {
            // If the socket buffer is full, bytes are already waiting: the loop will wake anyway.
            let _ = (&self.wake_writer).write(&[1]);
        }
    }
}

/// The server, as Python sees it: `Server(fd)` starts accepting
/// connections on an already listening socket, in a Rust thread; the
/// asyncio loop watches `fileno()` and calls `take()` when it becomes
/// readable.
#[pyclass(module = "korvex._core")]
pub struct Server {
    shared: Arc<Shared>,
    wake_reader: UnixStream,
    stop: Arc<Notify>,
    thread: Option<JoinHandle<std::io::Result<()>>>,
}

#[pymethods]
impl Server {
    /// `fd` is a listening socket, TCP or Unix (`unix`), opened by Python;
    /// the server takes ownership of it. Opening it in Python lets several
    /// worker processes share one socket. Request bodies are limited to
    /// `max_body` bytes, unless `body_limits` (`(method, pattern, bytes)`)
    /// sets a route's own; clients get `timeout` seconds to send the
    /// headers, then the body. Forwarding headers are believed from the
    /// `trusted` proxy addresses (`"*"` for all) and Unix sockets, the
    /// forwarded host only with `forwarded_host`. `constraints` are the
    /// app's own, as `(name, pattern)`, for the patterns of `body_limits`. Open WebSocket
    /// connections get a ping every `ws_ping_interval` seconds (0: never),
    /// and are closed if the client stays silent until the next one. At
    /// most `max_connections` connections are open at once (0: no limit);
    /// the others wait in the listen backlog.
    #[new]
    #[allow(clippy::needless_pass_by_value)] // PyO3 hands over owned values
    #[allow(clippy::too_many_arguments)] // one per App.run() option it serves
    fn new(
        fd: RawFd,
        unix: bool,
        max_body: u64,
        timeout: f64,
        body_limits: Vec<(String, String, u64)>,
        trusted: Vec<String>,
        ws_ping_interval: f64,
        forwarded_host: bool,
        constraints: Vec<(String, String)>,
        max_connections: usize,
    ) -> PyResult<Self> {
        let mut defined = Constraints::default();
        for (name, pattern) in &constraints {
            defined
                .define(name, pattern)
                .map_err(PyValueError::new_err)?;
        }
        let trusted = Trusted::parse(&trusted, forwarded_host).map_err(PyValueError::new_err)?;
        let mut limits = Node::default();
        for (method, pattern, bytes) in body_limits {
            let parsed = Method::parse(&method)
                .ok_or_else(|| PyValueError::new_err(format!("unknown HTTP method: {method:?}")))?;
            limits
                .insert_with(&pattern, parsed, bytes, &defined)
                .map_err(|e| PyValueError::new_err(e.to_string()))?;
        }
        if !(timeout.is_finite() && timeout > 0.0) {
            return Err(PyValueError::new_err(
                "timeout must be a positive number of seconds",
            ));
        }
        if !(ws_ping_interval.is_finite() && ws_ping_interval >= 0.0) {
            return Err(PyValueError::new_err(
                "ws_ping_interval must be a number of seconds, or 0 for no pings",
            ));
        }
        // SAFETY: Python hands over a listening socket it no longer uses
        // (`socket.detach()`), so this listener is its only owner.
        let listener = unsafe {
            if unix {
                StdListener::Unix(StdUnixListener::from_raw_fd(fd))
            } else {
                StdListener::Tcp(StdTcpListener::from_raw_fd(fd))
            }
        };
        listener.set_nonblocking()?;
        let (wake_reader, wake_writer) = UnixStream::pair()?;
        wake_reader.set_nonblocking(true)?;
        wake_writer.set_nonblocking(true)?;

        let shared = Arc::new(Shared {
            queue: Mutex::new(VecDeque::new()),
            woken: AtomicBool::new(false),
            wake_writer,
            grace_ms: AtomicU64::new(0),
            finished: AtomicBool::new(false),
            max_body,
            body_limits: limits,
            timeout: Duration::from_secs_f64(timeout),
            ws_ping_interval: (ws_ping_interval > 0.0)
                .then(|| Duration::from_secs_f64(ws_ping_interval)),
            trusted,
            next_id: AtomicU64::new(0),
            connections: (max_connections > 0).then(|| Arc::new(Semaphore::new(max_connections))),
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

    /// Every event waiting, as tuples whose first item says what it is:
    /// `("http", method, path, query_string, headers, body, client, scheme,
    /// responder, error)` (`error`: a status to answer with, or 0), `("ws", path, query_string, headers, client, scheme,
    /// connection)`, `("ws_message", id, text_or_bytes)` or
    /// `("ws_closed", id, code)` or `("writable", id)`.
    fn take<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        // Order matters, or a wake-up gets lost: empty the socket, then clear
        // the flag, then drain the queue. An event queued after the flag is
        // cleared writes a new byte, so the loop wakes again (at worst once
        // for nothing). Clearing the flag first would let this call swallow
        // the byte of an event it then drains, leaving the flag set with no
        // byte on its way: the next event would never wake the loop.
        let mut buffer = [0u8; 256];
        while matches!((&self.wake_reader).read(&mut buffer), Ok(n) if n > 0) {}
        self.shared.woken.store(false, Ordering::Release);

        let events: Vec<Event> = self
            .shared
            .queue
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .drain(..)
            .collect();
        let items = PyList::empty(py);
        for event in events {
            match event {
                Event::Request(request) => items.append((
                    "http",
                    request.method,
                    request.path,
                    PyBytes::new(py, &request.query),
                    header_list(py, &request.headers)?,
                    PyBytes::new(py, &request.body),
                    request.client,
                    request.scheme,
                    Responder {
                        id: self.shared.next_id.fetch_add(1, Ordering::Relaxed),
                        shared: Arc::clone(&self.shared),
                        reply: Mutex::new(Some(request.reply)),
                        stream: Mutex::new(None),
                    },
                    request.error,
                ))?,
                Event::WebSocket(opening) => items.append((
                    "ws",
                    opening.path,
                    PyBytes::new(py, &opening.query),
                    header_list(py, &opening.headers)?,
                    opening.client,
                    opening.scheme,
                    opening.connection,
                ))?,
                Event::WebSocketMessage(id, Incoming::Text(text)) => {
                    items.append(("ws_message", id, text))?;
                }
                Event::WebSocketMessage(id, Incoming::Binary(data)) => {
                    items.append(("ws_message", id, PyBytes::new(py, &data)))?;
                }
                Event::WebSocketMessage(id, Incoming::Closed(code)) => {
                    items.append(("ws_closed", id, code))?;
                }
                Event::Writable(id) => items.append(("writable", id))?,
            }
        }
        Ok(items)
    }

    /// Stops accepting connections; open ones finish their current request,
    /// for up to `grace` seconds, then close. Returns right away: Python
    /// keeps answering requests until `finished()` is true.
    fn stop(&self, grace: f64) {
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        // a delay in ms fits easily
        let grace_ms = (grace.max(0.0) * 1000.0) as u64;
        self.shared.grace_ms.store(grace_ms, Ordering::Release);
        self.stop.notify_one();
    }

    /// Whether the server has stopped and closed every connection.
    fn finished(&self) -> bool {
        self.shared.finished.load(Ordering::Acquire)
    }

    /// Stops the server if it's still running and waits for its thread.
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
    /// For the `("writable", id)` event a waiting stream gets.
    #[pyo3(get)]
    id: u64,
    shared: Arc<Shared>,
    reply: Mutex<Option<oneshot::Sender<Reply>>>,
    stream: Mutex<Option<(mpsc::UnboundedSender<Bytes>, Arc<Backlog>)>>,
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
        let backlog = Arc::new(Backlog::new(self.id, Arc::clone(&self.shared)));
        *self.stream.lock().unwrap_or_else(PoisonError::into_inner) =
            Some((sender, Arc::clone(&backlog)));
        self.send(
            status,
            headers,
            Either::Right(ChannelBody { receiver, backlog }),
        );
    }

    /// Sends one piece of a streamed body. Returns 1 (sent), 2 (sent, but
    /// the client is behind: wait for `("writable", id)` before the next
    /// one) or 0 once the client has gone, so Python can stop producing.
    fn send_chunk(&self, chunk: &[u8]) -> u8 {
        let stream = self.stream.lock().unwrap_or_else(PoisonError::into_inner);
        let Some((sender, backlog)) = stream.as_ref() else {
            return GONE;
        };
        // Counted before it's queued: the body can't take it out first.
        let next = backlog.add(chunk.len());
        if sender.send(Bytes::copy_from_slice(chunk)).is_err() {
            return GONE;
        }
        next
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
    backlog: Arc<Backlog>,
}

impl Body for ChannelBody {
    type Data = Bytes;
    type Error = Infallible;

    fn poll_frame(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Result<Frame<Bytes>, Infallible>>> {
        // hyper asks for the next piece once it has written the last one.
        let polled = self.receiver.poll_recv(cx);
        if let Poll::Ready(Some(bytes)) = &polled {
            self.backlog.taken(bytes.len());
        }
        polled.map(|chunk| chunk.map(|bytes| Ok(Frame::data(bytes))))
    }
}

impl Drop for ChannelBody {
    fn drop(&mut self) {
        // The client left: a waiting Python learns it from its next send_chunk().
        self.backlog.wake();
    }
}

fn header_list<'py>(py: Python<'py>, headers: &Headers) -> PyResult<Bound<'py, PyList>> {
    let list = PyList::empty(py);
    for (name, value) in headers {
        list.append((PyBytes::new(py, name), PyBytes::new(py, value)))?;
    }
    Ok(list)
}

/// The listening socket as handed over by Python, before tokio takes it.
enum StdListener {
    Tcp(StdTcpListener),
    Unix(StdUnixListener),
}

impl StdListener {
    fn set_nonblocking(&self) -> std::io::Result<()> {
        match self {
            Self::Tcp(listener) => listener.set_nonblocking(true),
            Self::Unix(listener) => listener.set_nonblocking(true),
        }
    }
}

enum Listener {
    Tcp(TcpListener),
    Unix(UnixListener),
}

impl Listener {
    /// Accepts one connection and serves it in its own task.
    async fn serve_next(&self, shared: &Arc<Shared>, shutdown: &watch::Sender<bool>) {
        // At the limit, wait for a connection to close before accepting
        // another: clients queue in the kernel's backlog meanwhile.
        let permit = match &shared.connections {
            Some(semaphore) => Semaphore::acquire_owned(Arc::clone(semaphore))
                .await
                .ok()
                .map(Arc::new),
            None => None,
        };
        // An accept error (e.g. too many open files) skips this connection only.
        // Pause briefly so a persistent error doesn't spin the CPU.
        let accepted = match self {
            Self::Tcp(listener) => listener.accept().await.map(|(stream, peer)| {
                let _ = stream.set_nodelay(true);
                serve_connection(stream, Some(peer), shared, shutdown.subscribe(), permit);
            }),
            Self::Unix(listener) => listener.accept().await.map(|(stream, _)| {
                serve_connection(stream, None, shared, shutdown.subscribe(), permit);
            }),
        };
        if accepted.is_err() {
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    }
}

/// A connection's slot under `max_connections`, kept until its last task
/// (a WebSocket outlives the HTTP connection it upgraded) is done.
pub type Permit = Option<Arc<OwnedSemaphorePermit>>;

/// On shutdown, how long a connection that has sent nothing yet gets to
/// deliver a request its client may already have written.
const FIRST_REQUEST_GRACE: Duration = Duration::from_secs(1);

/// Whether a connection has received a request yet. The `Notify` wakes the
/// shutdown wait; it stores a permit, so a request seen first is not lost.
#[derive(Default)]
struct Started(Notify, AtomicBool);

impl Started {
    /// Records that a request arrived, waking a pending shutdown once.
    fn mark(&self) {
        if !self.1.swap(true, Ordering::Relaxed) {
            self.0.notify_one();
        }
    }

    /// Tells whether any request has arrived on the connection.
    fn is_set(&self) -> bool {
        self.1.load(Ordering::Relaxed)
    }
}

/// Serves one connection in its own task. It holds a `shutdown` receiver
/// while it runs, which is how `accept_loop` knows when all are done, and
/// the `permit` of its slot.
fn serve_connection<S>(
    stream: S,
    peer: Option<SocketAddr>,
    shared: &Arc<Shared>,
    mut shutdown: watch::Receiver<bool>,
    permit: Permit,
) where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let shared = Arc::clone(shared);
    let timeout = shared.timeout;
    let for_websockets = shutdown.clone();
    let started = Arc::new(Started::default());
    let seen = Arc::clone(&started);
    tokio::spawn(async move {
        let service = service_fn(move |request| {
            seen.mark();
            handle(
                request,
                peer,
                Arc::clone(&shared),
                for_websockets.clone(),
                permit.clone(),
            )
        });
        // Covers slow headers and idle keep-alive connections alike.
        let connection = http1::Builder::new()
            .timer(TokioTimer::new())
            .header_read_timeout(timeout)
            .serve_connection(TokioIo::new(stream), service)
            .with_upgrades();
        let mut connection = std::pin::pin!(connection);
        // A client closing its connection early isn't an error worth reporting.
        tokio::select! {
            _ = connection.as_mut() => {}
            _ = shutdown.changed() => {
                // A connection that has sent nothing yet may hold a request
                // the client already wrote: wait for it before closing, but
                // only briefly, since browsers open connections they may
                // never use.
                if !started.is_set() {
                    tokio::select! {
                        _ = connection.as_mut() => return,
                        () = started.0.notified() => {}
                        () = tokio::time::sleep(FIRST_REQUEST_GRACE) => {}
                    }
                }
                // Finish the request in progress, then close (no more keep-alive).
                connection.as_mut().graceful_shutdown();
                let _ = connection.await;
            }
        }
    });
}

fn accept_loop(listener: StdListener, shared: &Arc<Shared>, stop: &Notify) -> std::io::Result<()> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    let result = runtime.block_on(async {
        let listener = match listener {
            StdListener::Tcp(listener) => Listener::Tcp(TcpListener::from_std(listener)?),
            StdListener::Unix(listener) => Listener::Unix(UnixListener::from_std(listener)?),
        };
        // Every connection and WebSocket holds a receiver: `closed()` resolves
        // once the last one is done.
        let (shutdown, unused) = watch::channel(false);
        drop(unused);
        loop {
            tokio::select! {
                () = listener.serve_next(shared, &shutdown) => {}
                () = stop.notified() => break,
            }
        }
        // Stop accepting; open connections finish their current request
        // (keep-alive ends after it) and WebSockets close with 1001. Give up
        // on them after the grace delay.
        drop(listener);
        let _ = shutdown.send(true);
        let grace = Duration::from_millis(shared.grace_ms.load(Ordering::Acquire));
        tokio::select! {
            () = shutdown.closed() => {}
            () = tokio::time::sleep(grace) => {}
        }
        Ok(())
    });
    shared.finished.store(true, Ordering::Release);
    result
}

async fn handle(
    request: Request<hyper::body::Incoming>,
    peer: Option<SocketAddr>,
    shared: Arc<Shared>,
    shutdown: watch::Receiver<bool>,
    permit: Permit,
) -> Result<Response<ResponseBody>, Infallible> {
    if websocket::is_upgrade(&request) {
        return Ok(websocket::handshake(request, peer, shared, shutdown, permit).await);
    }
    let (parts, body) = request.into_parts();
    let resolved = resolve(peer, &parts.headers, &shared.trusted);
    // Read the whole body before Python sees the request, within the size
    // limit and the timeout, so a slow or huge upload never holds a worker.
    // A body over the limit, too slow or broken still goes to the app,
    // without the body, so its error handlers, hooks and logs see it.
    let limit = shared.body_limit(parts.method.as_str(), parts.uri.path());
    let announced = parts
        .headers
        .get(CONTENT_LENGTH)
        .and_then(|v| v.to_str().ok()?.parse::<u64>().ok());
    let (body, error) = if announced.is_some_and(|length| length > limit) {
        (Bytes::new(), StatusCode::PAYLOAD_TOO_LARGE)
    } else {
        #[allow(clippy::cast_possible_truncation)]
        // limits beyond usize::MAX can't be held in memory anyway
        let reading = Limited::new(body, limit as usize).collect();
        match tokio::time::timeout(shared.timeout, reading).await {
            Err(_) => (Bytes::new(), StatusCode::REQUEST_TIMEOUT),
            Ok(Err(error)) if error.downcast_ref::<LengthLimitError>().is_some() => {
                (Bytes::new(), StatusCode::PAYLOAD_TOO_LARGE)
            }
            Ok(Err(_)) => (Bytes::new(), StatusCode::BAD_REQUEST),
            Ok(Ok(collected)) => (collected.to_bytes(), StatusCode::OK),
        }
    };
    let (reply, answer) = oneshot::channel();
    shared.push(Event::Request(Pending {
        method: parts.method.as_str().to_owned(),
        path: parts.uri.path().to_owned(),
        query: parts.uri.query().unwrap_or("").as_bytes().to_vec(),
        headers: request_headers(&parts.headers, resolved.host.as_deref()),
        body,
        client: resolved.client,
        scheme: resolved.scheme,
        error: if error == StatusCode::OK {
            0
        } else {
            error.as_u16()
        },
        reply,
    }));
    // An error means Python dropped the request without answering (it raised).
    let mut response = match answer.await {
        Ok(Reply {
            status,
            headers,
            body,
        }) => build(status, &headers, body),
        Err(_) => internal_error(),
    };
    if error != StatusCode::OK {
        // The rest of the body wasn't read: the connection can't be reused.
        response.headers_mut().insert(
            hyper::header::CONNECTION,
            hyper::header::HeaderValue::from_static("close"),
        );
    }
    Ok(response)
}

/// The request's headers for Python. `host`, when a trusted proxy passed
/// the one the client asked for, replaces the `Host` header: the app sees
/// the host the client used, as it sees the client's address and scheme.
pub fn request_headers(headers: &hyper::HeaderMap, host: Option<&str>) -> Headers {
    let mut list: Headers = headers
        .iter()
        .filter(|(name, _)| host.is_none() || *name != hyper::header::HOST)
        .map(|(name, value)| (name.as_str().as_bytes().to_vec(), value.as_bytes().to_vec()))
        .collect();
    if let Some(host) = host {
        list.insert(0, (b"host".to_vec(), host.as_bytes().to_vec()));
    }
    list
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

pub fn internal_error() -> Response<ResponseBody> {
    plain(StatusCode::INTERNAL_SERVER_ERROR, "Internal Server Error")
}

/// An error answered by Rust itself, before Python sees the request.
pub fn plain(status: StatusCode, text: &'static str) -> Response<ResponseBody> {
    let mut response = Response::new(Either::Left(Full::new(Bytes::from_static(text.as_bytes()))));
    *response.status_mut() = status;
    response.headers_mut().insert(
        hyper::header::CONTENT_TYPE,
        hyper::header::HeaderValue::from_static("text/plain; charset=utf-8"),
    );
    response
}
