//! WebSocket connections in the built-in server.
//!
//! The handshake request goes to Python like any request, as an opening
//! event; the app accepts or refuses it through its `WsConnection`. Once
//! accepted, Rust answers `101 Switching Protocols`, takes over the
//! connection, and relays messages: incoming ones through the same queue
//! and wake-up as requests, outgoing ones over a channel from Python.

use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex, PoisonError};

use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use http_body_util::{Either, Full};
use hyper::body::Incoming as Body;
use hyper::header::{
    CONNECTION, HeaderValue, SEC_WEBSOCKET_ACCEPT, SEC_WEBSOCKET_KEY, SEC_WEBSOCKET_PROTOCOL,
    SEC_WEBSOCKET_VERSION, UPGRADE,
};
use hyper::{Method, Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use pyo3::prelude::*;
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::sync::{mpsc, oneshot, watch};
use tokio_tungstenite::WebSocketStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::handshake::derive_accept_key;
use tokio_tungstenite::tungstenite::protocol::frame::CloseFrame;
use tokio_tungstenite::tungstenite::protocol::{Role, WebSocketConfig};

use crate::proxy::resolve;
use crate::server::{Event, Headers, ResponseBody, Shared, internal_error, plain, request_headers};

/// What the client sent on an open WebSocket, for Python.
pub enum Incoming {
    Text(String),
    Binary(Bytes),
    /// The connection is over, with this close code.
    Closed(u16),
}

/// A handshake waiting for the app to accept or refuse it.
pub struct Opening {
    pub path: String,
    pub query: Vec<u8>,
    pub headers: Headers,
    pub client: Option<(String, u16)>,
    pub scheme: &'static str,
    pub connection: WsConnection,
}

enum Decision {
    Accept {
        subprotocol: Option<String>,
        headers: Headers,
    },
    Refuse,
}

enum Outgoing {
    Text(String),
    Binary(Bytes),
    Close(u16, String),
}

/// How Python drives one WebSocket: `accept()` or `close()` first (closing
/// before accepting refuses it with a 403), then `send_text()`,
/// `send_bytes()` and `close()`. Dropping it closes the connection.
#[pyclass(frozen, module = "korvex._core")]
pub struct WsConnection {
    #[pyo3(get)]
    id: u64,
    decision: Mutex<Option<oneshot::Sender<Decision>>>,
    outgoing: Mutex<Option<mpsc::UnboundedSender<Outgoing>>>,
}

impl WsConnection {
    fn decide(&self, decision: Decision) -> bool {
        match self
            .decision
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take()
        {
            Some(sender) => sender.send(decision).is_ok(),
            None => false,
        }
    }

    fn send(&self, message: Outgoing) -> bool {
        self.outgoing
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .as_ref()
            .is_some_and(|sender| sender.send(message).is_ok())
    }
}

#[pymethods]
impl WsConnection {
    #[pyo3(signature = (subprotocol = None, headers = Vec::new()))]
    fn accept(&self, subprotocol: Option<String>, headers: Headers) {
        self.decide(Decision::Accept {
            subprotocol,
            headers,
        });
    }

    /// Returns `False` once the connection is gone.
    fn send_text(&self, text: String) -> bool {
        self.send(Outgoing::Text(text))
    }

    /// Returns `False` once the connection is gone.
    fn send_bytes(&self, data: &[u8]) -> bool {
        self.send(Outgoing::Binary(Bytes::copy_from_slice(data)))
    }

    /// Closes the connection, or refuses it if it wasn't accepted yet.
    fn close(&self, code: u16, reason: String) {
        if !self.decide(Decision::Refuse) {
            self.send(Outgoing::Close(code, reason));
        }
        self.outgoing
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take();
    }

    /// Called when the handler is done: refuses a connection never
    /// answered, closes an accepted one.
    fn finish(&self) {
        self.decide(Decision::Refuse);
        self.outgoing
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take();
    }
}

/// Whether `request` asks to switch to the WebSocket protocol.
pub fn is_upgrade(request: &Request<Body>) -> bool {
    let has_token = |name, token: &str| {
        request.headers().get_all(name).iter().any(|value| {
            value
                .to_str()
                .is_ok_and(|v| v.split(',').any(|t| t.trim().eq_ignore_ascii_case(token)))
        })
    };
    request.method() == Method::GET
        && has_token(UPGRADE, "websocket")
        && has_token(CONNECTION, "upgrade")
        && request.headers().contains_key(SEC_WEBSOCKET_KEY)
        && request
            .headers()
            .get(SEC_WEBSOCKET_VERSION)
            .is_some_and(|v| v == "13")
}

/// Hands the handshake to Python and answers with its decision: 101 and a
/// relay task, or 403.
pub async fn handshake(
    mut request: Request<Body>,
    peer: Option<std::net::SocketAddr>,
    shared: Arc<Shared>,
    shutdown: watch::Receiver<bool>,
) -> Response<ResponseBody> {
    let accept_key = request
        .headers()
        .get(SEC_WEBSOCKET_KEY)
        .map(|key| derive_accept_key(key.as_bytes()))
        .unwrap_or_default();
    let on_upgrade = hyper::upgrade::on(&mut request);
    let (parts, _) = request.into_parts();
    let resolved = resolve(peer, &parts.headers, &shared.trusted);
    let id = shared.next_websocket.fetch_add(1, Ordering::Relaxed);
    let (decision, decided) = oneshot::channel();
    let (outgoing, to_send) = mpsc::unbounded_channel();
    shared.push(Event::WebSocket(Opening {
        path: parts.uri.path().to_owned(),
        query: parts.uri.query().unwrap_or("").as_bytes().to_vec(),
        headers: request_headers(&parts.headers, resolved.host.as_deref()),
        client: resolved.client,
        scheme: if resolved.scheme == "https" {
            "wss"
        } else {
            "ws"
        },
        connection: WsConnection {
            id,
            decision: Mutex::new(Some(decision)),
            outgoing: Mutex::new(Some(outgoing)),
        },
    }));

    let Ok(Decision::Accept {
        subprotocol,
        headers,
    }) = decided.await
    else {
        return plain(StatusCode::FORBIDDEN, "Forbidden");
    };
    tokio::spawn(async move {
        let Ok(upgraded) = on_upgrade.await else {
            shared.push(Event::WebSocketMessage(id, Incoming::Closed(1006)));
            return;
        };
        #[allow(clippy::cast_possible_truncation)] // limits beyond usize::MAX can't be held anyway
        let limit = shared.max_body as usize;
        let config = WebSocketConfig::default()
            .max_message_size(Some(limit))
            .max_frame_size(Some(limit));
        let socket =
            WebSocketStream::from_raw_socket(TokioIo::new(upgraded), Role::Server, Some(config))
                .await;
        relay(id, socket, to_send, &shared, shutdown).await;
    });

    let mut builder = Response::builder()
        .status(StatusCode::SWITCHING_PROTOCOLS)
        .header(UPGRADE, HeaderValue::from_static("websocket"))
        .header(CONNECTION, HeaderValue::from_static("Upgrade"))
        .header(SEC_WEBSOCKET_ACCEPT, accept_key);
    if let Some(subprotocol) = subprotocol {
        builder = builder.header(SEC_WEBSOCKET_PROTOCOL, subprotocol);
    }
    for (name, value) in &headers {
        builder = builder.header(name.as_slice(), value.as_slice());
    }
    builder
        .body(Either::Left(Full::new(Bytes::new())))
        .unwrap_or_else(|_| internal_error())
}

/// Relays messages between the client and Python until either side closes
/// or the server shuts down, then tells Python the connection is over.
async fn relay<S>(
    id: u64,
    socket: WebSocketStream<S>,
    mut to_send: mpsc::UnboundedReceiver<Outgoing>,
    shared: &Shared,
    mut shutdown: watch::Receiver<bool>,
) where
    S: AsyncRead + AsyncWrite + Unpin,
{
    let close = |code: u16, reason: &str| {
        Message::Close(Some(CloseFrame {
            code: code.into(),
            reason: reason.to_owned().into(),
        }))
    };
    let (mut sink, mut stream) = socket.split();
    let code = loop {
        tokio::select! {
            incoming = stream.next() => match incoming {
                Some(Ok(Message::Text(text))) => {
                    shared.push(Event::WebSocketMessage(id, Incoming::Text(text.as_str().to_owned())));
                }
                Some(Ok(Message::Binary(data))) => shared.push(Event::WebSocketMessage(id, Incoming::Binary(data))),
                // tungstenite queues the answer to a ping: flushing sends it.
                Some(Ok(Message::Ping(_) | Message::Pong(_) | Message::Frame(_))) => {
                    let _ = sink.flush().await;
                }
                Some(Ok(Message::Close(frame))) => break frame.map_or(1005, |frame| u16::from(frame.code)),
                Some(Err(_)) | None => break 1006, // closed without a close frame
            },
            outgoing = to_send.recv() => match outgoing {
                Some(Outgoing::Text(text)) => {
                    if sink.send(Message::text(text)).await.is_err() { break 1006; }
                }
                Some(Outgoing::Binary(data)) => {
                    if sink.send(Message::binary(data)).await.is_err() { break 1006; }
                }
                Some(Outgoing::Close(code, reason)) => {
                    let _ = sink.send(close(code, &reason)).await;
                    break code;
                }
                None => {
                    let _ = sink.send(close(1000, "")).await; // Python is done with it
                    break 1000;
                }
            },
            _ = shutdown.changed() => {
                let _ = sink.send(close(1001, "server shutting down")).await;
                break 1001;
            }
        }
    };
    shared.push(Event::WebSocketMessage(id, Incoming::Closed(code)));
}
