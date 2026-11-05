//! Who the client really is, behind a reverse proxy.
//!
//! A proxy connects to the server itself, so the connection's address is
//! the proxy's; it passes the client's address in `X-Forwarded-For` and
//! the original scheme in `X-Forwarded-Proto`. Those headers are only
//! believed when the connection comes from a trusted proxy: anyone else
//! could write them to pretend to be someone else.

use std::net::{IpAddr, SocketAddr};

use hyper::HeaderMap;

/// The proxies whose `X-Forwarded-*` headers are believed. Connections
/// over a Unix socket are always trusted: only a local process can open
/// one.
#[derive(Debug)]
pub struct Trusted {
    all: bool,
    addresses: Vec<IpAddr>,
}

impl Trusted {
    /// From addresses like `"127.0.0.1"`, or `"*"` to trust everyone.
    pub fn parse(items: &[String]) -> Result<Self, String> {
        let mut trusted = Self {
            all: false,
            addresses: Vec::new(),
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
}

/// The client's address (`None` over a Unix socket without proxy headers)
/// and the scheme it used.
pub fn resolve(
    peer: Option<SocketAddr>,
    headers: &HeaderMap,
    trusted: &Trusted,
) -> (Option<(String, u16)>, &'static str) {
    let mut client = peer.map(|address| (address.ip().to_string(), address.port()));
    let mut scheme = "http";
    if peer.is_some_and(|address| !trusted.trusts(address.ip())) {
        return (client, scheme);
    }

    if first_value(headers, "x-forwarded-proto")
        .is_some_and(|proto| proto.eq_ignore_ascii_case("https"))
    {
        scheme = "https";
    }

    // Each proxy appends the address it saw, so the list reads client,
    // proxy 1, proxy 2... Walking from the right, skip the trusted proxies:
    // the first other entry is the client. Anything left of it may have
    // been written by the client itself.
    let entries: Vec<&str> = headers
        .get_all("x-forwarded-for")
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|value| value.split(','))
        .map(str::trim)
        .filter(|entry| !entry.is_empty())
        .collect();
    let untrusted = entries
        .iter()
        .rev()
        .find(|entry| entry.parse().map_or(true, |ip| !trusted.trusts(ip)));
    if let Some(entry) = untrusted.or(entries.first()) {
        client = Some(((*entry).to_string(), 0));
    }
    (client, scheme)
}

fn first_value<'a>(headers: &'a HeaderMap, name: &str) -> Option<&'a str> {
    let value = headers.get(name)?.to_str().ok()?;
    Some(value.split(',').next()?.trim())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn trusted(items: &[&str]) -> Trusted {
        Trusted::parse(&items.iter().map(ToString::to_string).collect::<Vec<_>>()).unwrap()
    }

    fn resolved(
        peer: &str,
        headers: &[(&'static str, &str)],
        trust: &[&str],
    ) -> (Option<(String, u16)>, &'static str) {
        let mut map = HeaderMap::new();
        for (name, value) in headers {
            map.append(*name, value.parse().unwrap());
        }
        let peer = (!peer.is_empty()).then(|| peer.parse().unwrap());
        resolve(peer, &map, &trusted(trust))
    }

    #[allow(clippy::unnecessary_wraps)] // reads better at the call sites, next to `None`
    fn client(host: &str, port: u16) -> Option<(String, u16)> {
        Some((host.to_string(), port))
    }

    #[test]
    fn without_a_proxy_the_connection_is_the_client() {
        assert_eq!(
            resolved("203.0.113.9:5000", &[], &["127.0.0.1"]),
            (client("203.0.113.9", 5000), "http")
        );
    }

    #[test]
    fn a_trusted_proxy_passes_the_client_and_scheme() {
        let headers = [
            ("x-forwarded-for", "203.0.113.9"),
            ("x-forwarded-proto", "https"),
        ];
        assert_eq!(
            resolved("127.0.0.1:5000", &headers, &["127.0.0.1"]),
            (client("203.0.113.9", 0), "https")
        );
    }

    #[test]
    fn headers_from_anyone_else_are_ignored() {
        let headers = [
            ("x-forwarded-for", "10.0.0.1"),
            ("x-forwarded-proto", "https"),
        ];
        assert_eq!(
            resolved("203.0.113.9:5000", &headers, &["127.0.0.1"]),
            (client("203.0.113.9", 5000), "http")
        );
    }

    #[test]
    fn an_address_written_by_the_client_itself_is_skipped() {
        // The client sent "X-Forwarded-For: 6.6.6.6"; the proxy appended the real address.
        let headers = [("x-forwarded-for", "6.6.6.6, 203.0.113.9")];
        assert_eq!(
            resolved("127.0.0.1:5000", &headers, &["127.0.0.1"]),
            (client("203.0.113.9", 0), "http")
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
            (client("203.0.113.9", 0), "http")
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
            (client("203.0.113.9", 0), "https")
        );
        assert_eq!(resolved("", &[], &[]), (None, "http"));
    }

    #[test]
    fn trusting_everyone_takes_the_leftmost_address() {
        let headers = [("x-forwarded-for", "203.0.113.9, 10.0.0.2")];
        assert_eq!(
            resolved("10.0.0.3:1", &headers, &["*"]),
            (client("203.0.113.9", 0), "http")
        );
    }

    #[test]
    fn only_http_and_https_are_accepted_as_schemes() {
        let headers = [("x-forwarded-proto", "javascript")];
        assert_eq!(resolved("127.0.0.1:1", &headers, &["127.0.0.1"]).1, "http");
    }

    #[test]
    fn rejects_invalid_trusted_addresses() {
        assert!(Trusted::parse(&["not-an-ip".to_string()]).is_err());
    }
}
