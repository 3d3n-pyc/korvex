//! Who the client really is, behind a reverse proxy.
//!
//! The connection's address is the proxy's; the proxy passes the client's in
//! headers: `X-Forwarded-For`, `-Proto` and `-Host`, or the standard
//! `Forwarded` (RFC 7239). They are only believed from a trusted proxy:
//! anyone else could forge them.
//!
//! A proxy only rewrites the headers it sets. nginx, Caddy and Traefik set
//! `X-Forwarded-For` and leave `Forwarded` alone; nginx leaves
//! `X-Forwarded-Host` alone too. So:
//! - `Forwarded` is only read when there's no `X-Forwarded-For` or
//!   `-Proto`: otherwise it may be the client's own;
//! - the forwarded host is only believed when the app says its proxy sets
//!   it (`forwarded_host`), as Django and Werkzeug do.

use std::net::{IpAddr, SocketAddr};

use hyper::HeaderMap;
use hyper::header::{FORWARDED, HeaderName};

// Looked up by `HeaderName`, not by `&str`: a string is parsed and checked
// again on every lookup, and these run on every request from a proxy.
const X_FORWARDED_FOR: HeaderName = HeaderName::from_static("x-forwarded-for");
const X_FORWARDED_PROTO: HeaderName = HeaderName::from_static("x-forwarded-proto");
const X_FORWARDED_HOST: HeaderName = HeaderName::from_static("x-forwarded-host");

/// The proxies whose forwarding headers are believed. Connections over a
/// Unix socket are always trusted: only a local process can open one.
#[derive(Debug)]
pub struct Trusted {
    all: bool,
    addresses: Vec<IpAddr>,
    /// Whether their forwarded host is believed: only if they set it.
    host: bool,
}

impl Trusted {
    /// From addresses like `"127.0.0.1"`, or `"*"` to trust everyone;
    /// `host` if those proxies set `X-Forwarded-Host` (or `Forwarded`'s
    /// `host`) themselves.
    pub fn parse(items: &[String], host: bool) -> Result<Self, String> {
        let mut trusted = Self {
            all: false,
            addresses: Vec::new(),
            host,
        };
        for item in items {
            if item == "*" {
                trusted.all = true;
            } else {
                let address = item
                    .parse()
                    .map_err(|_| format!("not an IP address: {item:?}"))?;
                trusted.addresses.push(address);
            }
        }
        Ok(trusted)
    }

    fn trusts(&self, address: IpAddr) -> bool {
        self.all || self.addresses.contains(&address)
    }

    /// Whether a forwarded address is a trusted proxy. One that isn't an IP
    /// address (`unknown`, an obfuscated `_name`) isn't, unless all are.
    fn trusts_entry(&self, entry: &str) -> bool {
        self.all
            || split_host_port(entry)
                .0
                .parse()
                .is_ok_and(|ip| self.trusts(ip))
    }
}

/// What the client used, as far as the server can tell.
#[derive(Debug, PartialEq, Eq)]
pub struct Resolved {
    /// `(host, port)` of the client; `None` over a Unix socket without
    /// proxy headers. The port is 0 when a proxy didn't pass it.
    pub client: Option<(String, u16)>,
    pub scheme: &'static str,
    /// The `Host` the client asked for, when a trusted proxy passed it.
    pub host: Option<String>,
}

pub fn resolve(peer: Option<SocketAddr>, headers: &HeaderMap, trusted: &Trusted) -> Resolved {
    let mut resolved = Resolved {
        client: peer.map(|address| (address.ip().to_string(), address.port())),
        scheme: "http",
        host: None,
    };
    if peer.is_some_and(|address| !trusted.trusts(address.ip())) {
        return resolved;
    }
    if headers.contains_key(X_FORWARDED_FOR) || headers.contains_key(X_FORWARDED_PROTO) {
        resolve_x_forwarded(headers, trusted, &mut resolved);
    } else {
        let elements = forwarded(headers);
        if elements.is_empty() {
            resolve_x_forwarded(headers, trusted, &mut resolved); // X-Forwarded-Host alone
        } else {
            resolve_forwarded(&elements, trusted, &mut resolved);
        }
    }
    if !trusted.host {
        resolved.host = None;
    }
    resolved
}

/// `X-Forwarded-For: client, proxy1, proxy2`, each proxy appending the
/// address it saw. Walking from the right, skip trusted proxies: the first
/// other entry is the client; anything left of it may be forged. For
/// `-Proto` and `-Host`, the rightmost value (the nearest proxy's) is believed.
fn resolve_x_forwarded(headers: &HeaderMap, trusted: &Trusted, resolved: &mut Resolved) {
    let entries = values(headers, &X_FORWARDED_FOR);
    let untrusted = entries
        .iter()
        .rev()
        .find(|entry| !trusted.trusts_entry(entry));
    if let Some(entry) = untrusted.or(entries.first()) {
        resolved.client = Some(((*entry).to_string(), 0));
    }
    if let Some(proto) = values(headers, &X_FORWARDED_PROTO).last() {
        resolved.scheme = scheme(proto);
    }
    resolved.host = values(headers, &X_FORWARDED_HOST)
        .last()
        .and_then(|host| valid_host(host));
}

/// `Forwarded: for=client;proto=https;host=example.com, for=proxy1`: one
/// element per proxy. The client's element is found the same way, from the
/// right; its `proto` and `host` are what the client used.
fn resolve_forwarded(elements: &[Element], trusted: &Trusted, resolved: &mut Resolved) {
    let client = elements
        .iter()
        .rev()
        .find(|element| {
            element
                .for_
                .as_deref()
                .is_none_or(|entry| !trusted.trusts_entry(entry))
        })
        .or(elements.first());
    let Some(element) = client else { return };
    if let Some(entry) = &element.for_ {
        let (host, port) = split_host_port(entry);
        resolved.client = Some((host.to_string(), port));
    }
    if let Some(proto) = &element.proto {
        resolved.scheme = scheme(proto);
    }
    resolved.host = element.host.as_deref().and_then(valid_host);
}

fn scheme(proto: &str) -> &'static str {
    if proto.eq_ignore_ascii_case("https") {
        "https"
    } else {
        "http"
    }
}

/// A host name or address, with an optional port: nothing else, so a
/// forwarded value can't smuggle anything into the `Host` the app sees.
fn valid_host(host: &str) -> Option<String> {
    let ok = !host.is_empty()
        && host.len() <= 255
        && host.bytes().all(|b| {
            b.is_ascii_alphanumeric() || matches!(b, b'.' | b'-' | b':' | b'[' | b']' | b'_')
        });
    ok.then(|| host.to_ascii_lowercase())
}

/// Every comma-separated value of a header, in order, over all its lines.
fn values<'a>(headers: &'a HeaderMap, name: &HeaderName) -> Vec<&'a str> {
    headers
        .get_all(name)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|value| value.split(','))
        .map(str::trim)
        .filter(|entry| !entry.is_empty())
        .collect()
}

/// `192.0.2.1:8080` -> `("192.0.2.1", 8080)`, `[2001:db8::1]:80` ->
/// `("2001:db8::1", 80)`; no port, or one that isn't a number, gives 0.
fn split_host_port(entry: &str) -> (&str, u16) {
    if let Some(rest) = entry.strip_prefix('[') {
        let (host, after) = rest.split_once(']').unwrap_or((rest, ""));
        return (
            host,
            after
                .strip_prefix(':')
                .and_then(|p| p.parse().ok())
                .unwrap_or(0),
        );
    }
    match entry.split_once(':') {
        Some((host, port)) if !port.contains(':') => (host, port.parse().unwrap_or(0)),
        _ => (entry, 0), // no port, or a bare IPv6 address
    }
}

/// One proxy's element of a `Forwarded` header.
#[derive(Debug, Default, PartialEq, Eq)]
struct Element {
    for_: Option<String>,
    proto: Option<String>,
    host: Option<String>,
}

/// The elements of every `Forwarded` header line, in order. Values can be
/// quoted (`for="[2001:db8::1]:80"`); unknown parameters are skipped.
///
/// A line with a quote left open (or ending on a backslash) makes the whole
/// header ignored: an open quote in the client's value would swallow the
/// element the proxy appends, so the trust walk would pick the client's own.
fn forwarded(headers: &HeaderMap) -> Vec<Element> {
    let mut elements = Vec::new();
    for line in headers
        .get_all(FORWARDED)
        .iter()
        .filter_map(|v| v.to_str().ok())
    {
        let Some(parts) = split_unquoted(line, ',') else {
            return Vec::new();
        };
        for part in parts {
            let mut element = Element::default();
            // A part of a balanced line is balanced too.
            for pair in split_unquoted(part, ';').unwrap_or_default() {
                let Some((name, value)) = pair.split_once('=') else {
                    continue;
                };
                let value = unquote(value.trim());
                match name.trim().to_ascii_lowercase().as_str() {
                    "for" => element.for_ = Some(value),
                    "proto" => element.proto = Some(value),
                    "host" => element.host = Some(value),
                    _ => {}
                }
            }
            if element != Element::default() {
                elements.push(element);
            }
        }
    }
    elements
}

/// Splits on `separator`, except inside double quotes; `None` if a quote
/// is left open or the text ends on an escape.
fn split_unquoted(text: &str, separator: char) -> Option<Vec<&str>> {
    let mut parts = Vec::new();
    let (mut start, mut quoted, mut escaped) = (0, false, false);
    for (index, c) in text.char_indices() {
        match c {
            _ if escaped => escaped = false,
            '\\' if quoted => escaped = true,
            '"' => quoted = !quoted,
            c if c == separator && !quoted => {
                parts.push(text[start..index].trim());
                start = index + c.len_utf8();
            }
            _ => {}
        }
    }
    if quoted || escaped {
        return None;
    }
    parts.push(text[start..].trim());
    parts.retain(|part| !part.is_empty());
    Some(parts)
}

fn unquote(value: &str) -> String {
    match value.strip_prefix('"').and_then(|v| v.strip_suffix('"')) {
        Some(inner) => {
            let mut out = String::with_capacity(inner.len());
            let mut chars = inner.chars();
            while let Some(c) = chars.next() {
                out.push(if c == '\\' {
                    chars.next().unwrap_or(c)
                } else {
                    c
                });
            }
            out
        }
        None => value.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn trusted(items: &[&str], host: bool) -> Trusted {
        Trusted::parse(
            &items.iter().map(ToString::to_string).collect::<Vec<_>>(),
            host,
        )
        .unwrap()
    }

    fn resolved_with(
        peer: &str,
        headers: &[(&'static str, &str)],
        trust: &[&str],
        host: bool,
    ) -> Resolved {
        let mut map = HeaderMap::new();
        for (name, value) in headers {
            map.append(*name, value.parse().unwrap());
        }
        let peer = (!peer.is_empty()).then(|| peer.parse().unwrap());
        resolve(peer, &map, &trusted(trust, host))
    }

    /// With the forwarded host believed, to test it with the rest.
    fn resolved(peer: &str, headers: &[(&'static str, &str)], trust: &[&str]) -> Resolved {
        resolved_with(peer, headers, trust, true)
    }

    #[allow(clippy::unnecessary_wraps)] // reads better at the call sites, next to `None`
    fn client(host: &str, port: u16) -> Option<(String, u16)> {
        Some((host.to_string(), port))
    }

    fn plain(client: Option<(String, u16)>, scheme: &'static str) -> Resolved {
        Resolved {
            client,
            scheme,
            host: None,
        }
    }

    #[test]
    fn without_a_proxy_the_connection_is_the_client() {
        assert_eq!(
            resolved("203.0.113.9:5000", &[], &["127.0.0.1"]),
            plain(client("203.0.113.9", 5000), "http")
        );
    }

    #[test]
    fn a_trusted_proxy_passes_the_client_scheme_and_host() {
        let headers = [
            ("x-forwarded-for", "203.0.113.9"),
            ("x-forwarded-proto", "https"),
            ("x-forwarded-host", "Example.com:8443"),
        ];
        assert_eq!(
            resolved("127.0.0.1:5000", &headers, &["127.0.0.1"]),
            Resolved {
                client: client("203.0.113.9", 0),
                scheme: "https",
                host: Some("example.com:8443".into())
            }
        );
    }

    #[test]
    fn headers_from_anyone_else_are_ignored() {
        let headers = [
            ("x-forwarded-for", "10.0.0.1"),
            ("x-forwarded-proto", "https"),
            ("x-forwarded-host", "evil.example"),
            ("forwarded", "for=10.0.0.1;proto=https;host=evil.example"),
        ];
        assert_eq!(
            resolved("203.0.113.9:5000", &headers, &["127.0.0.1"]),
            plain(client("203.0.113.9", 5000), "http")
        );
    }

    #[test]
    fn an_address_written_by_the_client_itself_is_skipped() {
        // The client sent "X-Forwarded-For: 6.6.6.6"; the proxy appended the real address.
        let headers = [("x-forwarded-for", "6.6.6.6, 203.0.113.9")];
        assert_eq!(
            resolved("127.0.0.1:5000", &headers, &["127.0.0.1"]),
            plain(client("203.0.113.9", 0), "http")
        );
    }

    #[test]
    fn the_rightmost_proto_and_host_are_believed() {
        // The client wrote its own values; the proxy appended the real ones.
        let headers = [
            ("x-forwarded-proto", "https, http"),
            ("x-forwarded-host", "evil.example"),
            ("x-forwarded-host", "shop.example"),
        ];
        let got = resolved("127.0.0.1:5000", &headers, &["127.0.0.1"]);
        assert_eq!(
            (got.scheme, got.host.as_deref()),
            ("http", Some("shop.example"))
        );
    }

    #[test]
    fn a_chain_of_trusted_proxies_is_walked_through() {
        let headers = [
            ("x-forwarded-for", "203.0.113.9, 10.0.0.2"),
            ("x-forwarded-for", "10.0.0.3"),
        ];
        let trust = ["127.0.0.1", "10.0.0.2", "10.0.0.3"];
        assert_eq!(
            resolved("127.0.0.1:5000", &headers, &trust),
            plain(client("203.0.113.9", 0), "http")
        );
    }

    #[test]
    fn unix_socket_connections_are_trusted() {
        let headers = [
            ("x-forwarded-for", "203.0.113.9"),
            ("x-forwarded-proto", "HTTPS"),
        ];
        assert_eq!(
            resolved("", &headers, &[]),
            plain(client("203.0.113.9", 0), "https")
        );
        assert_eq!(resolved("", &[], &[]), plain(None, "http"));
    }

    #[test]
    fn trusting_everyone_takes_the_leftmost_address() {
        let headers = [("x-forwarded-for", "203.0.113.9, 10.0.0.2")];
        assert_eq!(
            resolved("10.0.0.3:1", &headers, &["*"]),
            plain(client("203.0.113.9", 0), "http")
        );
    }

    #[test]
    fn only_http_and_https_are_accepted_as_schemes() {
        let headers = [("x-forwarded-proto", "javascript")];
        assert_eq!(
            resolved("127.0.0.1:1", &headers, &["127.0.0.1"]).scheme,
            "http"
        );
    }

    #[test]
    fn a_host_with_anything_but_name_characters_is_ignored() {
        for host in ["a b", "x.example/evil", "x.example\"", "x@y", ""] {
            let headers = [("x-forwarded-host", host)];
            assert_eq!(
                resolved("127.0.0.1:1", &headers, &["127.0.0.1"]).host,
                None,
                "{host:?}"
            );
        }
    }

    #[test]
    fn rejects_invalid_trusted_addresses() {
        assert!(Trusted::parse(&["not-an-ip".to_string()], false).is_err());
    }

    #[test]
    fn the_forwarded_header() {
        let headers = [(
            "forwarded",
            "for=203.0.113.9:4711;proto=https;host=shop.example",
        )];
        assert_eq!(
            resolved("127.0.0.1:1", &headers, &["127.0.0.1"]),
            Resolved {
                client: client("203.0.113.9", 4711),
                scheme: "https",
                host: Some("shop.example".into())
            }
        );
    }

    #[test]
    fn forwarded_with_quotes_ipv6_and_a_chain() {
        // The client wrote the first element; proxy 10.0.0.2 the last one.
        let headers = [
            ("forwarded", "for=6.6.6.6;host=evil.example"),
            (
                "forwarded",
                r#"for="[2001:db8::17]:4711";proto=https;host="shop.example", for=10.0.0.2"#,
            ),
        ];
        let got = resolved("127.0.0.1:1", &headers, &["127.0.0.1", "10.0.0.2"]);
        assert_eq!(
            got,
            Resolved {
                client: client("2001:db8::17", 4711),
                scheme: "https",
                host: Some("shop.example".into())
            }
        );
    }

    #[test]
    fn forwarded_is_ignored_next_to_x_forwarded_headers() {
        // The proxy set X-Forwarded-For; Forwarded came from the client, untouched.
        let headers = [
            ("x-forwarded-for", "198.51.100.1"),
            ("forwarded", "for=6.6.6.6;proto=https;host=evil.example"),
        ];
        assert_eq!(
            resolved("127.0.0.1:1", &headers, &["127.0.0.1"]),
            plain(client("198.51.100.1", 0), "http")
        );
        let headers = [("x-forwarded-proto", "http"), ("forwarded", "for=6.6.6.6")];
        assert_eq!(
            resolved("127.0.0.1:1", &headers, &["127.0.0.1"]).client,
            client("127.0.0.1", 1)
        );
    }

    #[test]
    fn a_quote_left_open_by_the_client_cant_hide_the_proxys_element() {
        // The client sent `Forwarded: for=6.6.6.6;host=evil.example;x="` and
        // the proxy appended its own element on the same line.
        let line = r#"for=6.6.6.6;host=evil.example;x=", for=203.0.113.9;host=shop.example"#;
        let got = resolved("127.0.0.1:1", &[("forwarded", line)], &["127.0.0.1"]);
        assert_eq!(got, plain(client("127.0.0.1", 1), "http")); // ignored, not believed
        // The same, without the trick: the proxy's element is the one believed.
        let line = "for=6.6.6.6;host=evil.example, for=203.0.113.9;host=shop.example";
        let got = resolved("127.0.0.1:1", &[("forwarded", line)], &["127.0.0.1"]);
        assert_eq!(
            (got.client, got.host.as_deref()),
            (client("203.0.113.9", 0), Some("shop.example"))
        );
    }

    #[test]
    fn the_forwarded_host_is_only_believed_when_asked() {
        let headers = [
            ("x-forwarded-for", "203.0.113.9"),
            ("x-forwarded-host", "evil.example"),
        ];
        assert_eq!(
            resolved_with("127.0.0.1:1", &headers, &["127.0.0.1"], false).host,
            None
        );
        let headers = [("forwarded", "for=203.0.113.9;host=evil.example")];
        assert_eq!(
            resolved_with("127.0.0.1:1", &headers, &["127.0.0.1"], false).host,
            None
        );
        assert_eq!(resolved_with("", &headers, &[], false).host, None); // a Unix socket too
    }

    #[test]
    fn obfuscated_and_unknown_clients_are_kept_as_given() {
        let headers = [("forwarded", "for=_hidden, for=unknown")];
        assert_eq!(
            resolved("127.0.0.1:1", &headers, &["127.0.0.1"]).client,
            client("unknown", 0)
        );
    }

    #[test]
    fn splitting_and_unquoting() {
        assert_eq!(
            split_unquoted(r#"a="x,y";b, c"#, ','),
            Some(vec![r#"a="x,y";b"#, "c"])
        );
        assert_eq!(split_unquoted(r#"a="open, b"#, ','), None);
        assert_eq!(split_unquoted(r#"a="x\"#, ','), None);
        assert_eq!(unquote(r#""a\"b""#), r#"a"b"#);
        assert_eq!(split_host_port("[::1]:80"), ("::1", 80));
        assert_eq!(split_host_port("::1"), ("::1", 0));
        assert_eq!(split_host_port("1.2.3.4:x"), ("1.2.3.4", 0));
    }
}
