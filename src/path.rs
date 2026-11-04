//! Path splitting and route pattern syntax.
//!
//! A pattern is a path whose segments may be:
//! - a literal: `users`
//! - a parameter: `{id}`, optionally constrained: `{id:int}`, `{id:uuid}`
//! - a wildcard, as the last segment only: `*filepath`

use std::fmt;

/// Iterates over the non-empty segments of a path:
/// `"/users//42/"` yields `"users"`, then `"42"`.
#[derive(Debug, Clone)]
pub struct Segments<'a> {
    rest: &'a str,
}

impl<'a> Segments<'a> {
    pub fn new(path: &'a str) -> Self {
        Self {
            rest: path.trim_matches('/'),
        }
    }

    /// The part of the path not consumed yet, as one slice. This is what a
    /// wildcard captures, so capturing never allocates.
    pub fn remainder(&self) -> &'a str {
        self.rest.trim_start_matches('/')
    }
}

impl<'a> Iterator for Segments<'a> {
    type Item = &'a str;

    fn next(&mut self) -> Option<&'a str> {
        while !self.rest.is_empty() {
            let (segment, tail) = self.rest.split_once('/').unwrap_or((self.rest, ""));
            self.rest = tail;
            if !segment.is_empty() {
                return Some(segment);
            }
        }
        None
    }
}

/// What a parameter's value must look like.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Constraint {
    /// Anything: `{id}` or `{id:str}`.
    Any,
    /// A signed integer: `{id:int}`.
    Int,
    /// A UUID, `8-4-4-4-12` hex digits: `{id:uuid}`.
    Uuid,
}

impl Constraint {
    fn parse(name: &str) -> Option<Self> {
        match name {
            "str" => Some(Self::Any),
            "int" => Some(Self::Int),
            "uuid" => Some(Self::Uuid),
            _ => None,
        }
    }

    pub fn matches(self, value: &str) -> bool {
        match self {
            Self::Any => true,
            Self::Int => value.parse::<i64>().is_ok(),
            Self::Uuid => is_uuid(value),
        }
    }

    /// Renders a parameter back to pattern syntax, e.g. `{id:int}`.
    pub fn format(self, name: &str) -> String {
        match self {
            Self::Any => format!("{{{name}}}"),
            Self::Int => format!("{{{name}:int}}"),
            Self::Uuid => format!("{{{name}:uuid}}"),
        }
    }
}

fn is_uuid(value: &str) -> bool {
    let mut groups = value.split('-');
    [8, 4, 4, 4, 12].into_iter().all(|len| {
        groups
            .next()
            .is_some_and(|g| g.len() == len && g.bytes().all(|b| b.is_ascii_hexdigit()))
    }) && groups.next().is_none()
}

/// One segment of a route pattern.
#[derive(Debug, PartialEq, Eq)]
pub enum Segment<'a> {
    Literal(&'a str),
    Param {
        name: &'a str,
        constraint: Constraint,
    },
    Wildcard(&'a str),
}

impl<'a> Segment<'a> {
    pub fn parse(raw: &'a str) -> Result<Self, PatternError> {
        if let Some(name) = raw.strip_prefix('*') {
            if name.is_empty() {
                return Err(PatternError::UnnamedWildcard);
            }
            return Ok(Self::Wildcard(name));
        }
        let Some(inner) = raw.strip_prefix('{').and_then(|s| s.strip_suffix('}')) else {
            return Ok(Self::Literal(raw));
        };
        let (name, constraint) = match inner.split_once(':') {
            None => (inner, Constraint::Any),
            Some((name, c)) => (
                name,
                Constraint::parse(c).ok_or_else(|| PatternError::UnknownConstraint(c.into()))?,
            ),
        };
        Ok(Self::Param { name, constraint })
    }
}

/// Why a route pattern was rejected.
#[derive(Debug, PartialEq, Eq)]
pub enum PatternError {
    /// `{id:float}`: not `int`, `uuid` or `str`.
    UnknownConstraint(String),
    /// `/users/{id}` and `/users/{slug}` both registered: a match could
    /// not tell which name to report.
    ConflictingNames { existing: String, new: String },
    /// A bare `*`.
    UnnamedWildcard,
    /// `/static/*path/more`: a wildcard swallows the rest of the path.
    WildcardNotLast,
}

impl fmt::Display for PatternError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnknownConstraint(c) => {
                write!(f, "unknown constraint {c:?} (expected int, uuid or str)")
            }
            Self::ConflictingNames { existing, new } => write!(
                f,
                "parameter {new:?} conflicts with {existing:?} already registered at this position"
            ),
            Self::UnnamedWildcard => write!(f, "a wildcard must be named, e.g. *filepath"),
            Self::WildcardNotLast => write!(f, "a wildcard must be the last segment"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn segments_skip_empty_parts() {
        let all = |p| Segments::new(p).collect::<Vec<_>>();
        assert_eq!(all("/users//42/"), ["users", "42"]);
        assert_eq!(all("users/42"), ["users", "42"]);
        assert_eq!(all("/"), [] as [&str; 0]);
    }

    #[test]
    fn remainder_is_the_unconsumed_suffix() {
        let mut segments = Segments::new("static/a/b.png");
        assert_eq!(segments.remainder(), "static/a/b.png");
        segments.next();
        assert_eq!(segments.remainder(), "a/b.png");
        segments.by_ref().for_each(drop);
        assert_eq!(segments.remainder(), "");
    }

    #[test]
    fn parses_each_segment_kind() {
        use Constraint::{Any, Int, Uuid};
        let param = |name, constraint| Ok(Segment::Param { name, constraint });
        assert_eq!(Segment::parse("users"), Ok(Segment::Literal("users")));
        assert_eq!(Segment::parse("{id}"), param("id", Any));
        assert_eq!(Segment::parse("{id:str}"), param("id", Any));
        assert_eq!(Segment::parse("{id:int}"), param("id", Int));
        assert_eq!(Segment::parse("{id:uuid}"), param("id", Uuid));
        assert_eq!(Segment::parse("*path"), Ok(Segment::Wildcard("path")));
    }

    #[test]
    fn rejects_bad_segments() {
        assert_eq!(
            Segment::parse("{id:float}"),
            Err(PatternError::UnknownConstraint("float".into()))
        );
        assert_eq!(Segment::parse("*"), Err(PatternError::UnnamedWildcard));
    }

    #[test]
    fn constraints_check_values() {
        assert!(Constraint::Any.matches("anything"));
        assert!(Constraint::Int.matches("42"));
        assert!(Constraint::Int.matches("-7"));
        assert!(!Constraint::Int.matches("4.2"));
        assert!(!Constraint::Int.matches("abc"));
        assert!(Constraint::Uuid.matches("123e4567-e89b-12d3-a456-426614174000"));
        assert!(!Constraint::Uuid.matches("123e4567-e89b-12d3-a456"));
        assert!(!Constraint::Uuid.matches("not-a-uuid"));
    }

    #[test]
    fn format_round_trips_through_parse() {
        for constraint in [Constraint::Any, Constraint::Int, Constraint::Uuid] {
            let pattern = constraint.format("id");
            assert_eq!(
                Segment::parse(&pattern),
                Ok(Segment::Param {
                    name: "id",
                    constraint
                })
            );
        }
    }
}
