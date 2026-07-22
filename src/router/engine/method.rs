//! The HTTP method a route is registered under.

/// An HTTP method a route can be registered under.
///
/// A closed, small set rather than an open `Custom(String)` escape hatch:
/// Korvex targets standard HTTP routing, and a fixed enum keeps method
/// comparisons integer-cheap instead of string ones.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Method {
    Get,
    Post,
    Put,
    Patch,
    Delete,
    Head,
    Options,
}

impl Method {
    /// Parses an uppercase HTTP method name, e.g. `"GET"`. Returns `None`
    /// for anything else — callers turn that into a `PyValueError` at the
    /// FFI boundary rather than silently defaulting.
    pub fn parse(method: &str) -> Option<Self> {
        Some(match method {
            "GET" => Self::Get,
            "POST" => Self::Post,
            "PUT" => Self::Put,
            "PATCH" => Self::Patch,
            "DELETE" => Self::Delete,
            "HEAD" => Self::Head,
            "OPTIONS" => Self::Options,
            _ => return None,
        })
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Get => "GET",
            Self::Post => "POST",
            Self::Put => "PUT",
            Self::Patch => "PATCH",
            Self::Delete => "DELETE",
            Self::Head => "HEAD",
            Self::Options => "OPTIONS",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_known_methods() {
        assert_eq!(Method::parse("GET"), Some(Method::Get));
        assert_eq!(Method::parse("DELETE"), Some(Method::Delete));
    }

    #[test]
    fn rejects_unknown_or_lowercase_methods() {
        assert_eq!(Method::parse("get"), None);
        assert_eq!(Method::parse("TRACE"), None);
    }

    #[test]
    fn as_str_round_trips_through_parse() {
        for method in [
            Method::Get,
            Method::Post,
            Method::Put,
            Method::Patch,
            Method::Delete,
            Method::Head,
            Method::Options,
        ] {
            assert_eq!(Method::parse(method.as_str()), Some(method));
        }
    }
}
