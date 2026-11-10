//! Fuzzes the proxy header resolution: it must never panic.
#![no_main]
#![allow(dead_code)]

#[path = "../../src/proxy.rs"]
mod proxy;

use arbitrary::Arbitrary;
use hyper::HeaderMap;
use hyper::header::{HeaderName, HeaderValue};
use libfuzzer_sys::fuzz_target;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};

const NAMES: [&str; 4] = [
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-host",
    "forwarded",
];

#[derive(Arbitrary, Debug)]
struct Input {
    peer: Option<(u8, u16)>,
    trusted: Vec<String>,
    trust_all: bool,
    host: bool,
    headers: Vec<(u8, Vec<u8>)>,
}

fuzz_target!(|input: Input| {
    let mut items = input.trusted;
    items.push("127.0.0.1".into());
    if input.trust_all {
        items.push("*".into());
    }
    let Ok(trusted) = proxy::Trusted::parse(&items, input.host) else {
        items.retain(|item| item.parse::<IpAddr>().is_ok() || item == "*");
        let trusted = proxy::Trusted::parse(&items, input.host).unwrap();
        run(input.peer, &input.headers, &trusted);
        return;
    };
    run(input.peer, &input.headers, &trusted);
});

fn run(peer: Option<(u8, u16)>, headers: &[(u8, Vec<u8>)], trusted: &proxy::Trusted) {
    let mut map = HeaderMap::new();
    for (name, value) in headers {
        let name = HeaderName::from_static(NAMES[usize::from(*name) % NAMES.len()]);
        if let Ok(value) = HeaderValue::from_bytes(value) {
            map.append(name, value);
        }
    }
    let peer =
        peer.map(|(last, port)| SocketAddr::new(Ipv4Addr::new(127, 0, 0, last % 2).into(), port));
    let _ = proxy::resolve(peer, &map, trusted);
}
