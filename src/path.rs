//! Path splitting and route pattern syntax.
//!
//! A pattern is a path whose segments may be:
//! - a literal: `users`
//! - a parameter: `{id}`, optionally constrained: `{id:int}`, `{id:uuid}`,
//!   or by a constraint the app defined: `{post:slug}`
//! - a wildcard, as the last segment only: `*filepath`

use std::fmt;

use regex::Regex;

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
    /// One the app defined (`Constraints::define`), by its index there.
    /// Matching it takes its regex, kept in the tree next to the parameter.
    Custom(u16),
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

    /// Whether `value` fits a built-in constraint. A custom one is matched
    /// with its regex instead (see `Constraints::regex`).
    pub fn matches(self, value: &str) -> bool {
        match self {
            Self::Any => true,
            Self::Int => value.parse::<i64>().is_ok(),
            Self::Uuid => is_uuid(value),
            Self::Custom(_) => false,
        }
    }

    /// Renders a parameter back to pattern syntax, e.g. `{id:int}`.
    pub fn format(self, name: &str, constraints: &Constraints) -> String {
        match self {
            Self::Any => format!("{{{name}}}"),
            Self::Int => format!("{{{name}:int}}"),
            Self::Uuid => format!("{{{name}:uuid}}"),
            Self::Custom(index) => {
                format!("{{{name}:{}}}", constraints.custom[usize::from(index)].name)
            }
        }
    }
}

/// A constraint defined by the app: a name and the regex a value must
/// match entirely.
#[derive(Debug, Clone)]
struct Custom {
    name: String,
    pattern: String,
    regex: Regex,
}

/// The constraints an app defined, beyond `int`, `uuid` and `str`. They
/// are defined before the routes that use them, and can't be redefined
/// differently: that would change routes already registered.
#[derive(Debug, Clone, Default)]
pub struct Constraints {
    custom: Vec<Custom>,
}

impl Constraints {
    /// Defines `name` as values matching `pattern` entirely (a regex, as
    /// the `regex` crate reads it: no look-around or backreferences, so
    /// matching takes linear time).
    pub fn define(&mut self, name: &str, pattern: &str) -> Result<(), String> {
        if Constraint::parse(name).is_some() {
            return Err(format!("{name:?} is a built-in constraint"));
        }
        if name.is_empty() || !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
            return Err(format!(
                "a constraint name is letters, digits and _, not {name:?}"
            ));
        }
        if let Some(existing) = self.custom.iter().find(|c| c.name == name) {
            if existing.pattern == pattern {
                return Ok(());
            }
            return Err(format!(
                "constraint {name:?} is already defined as {:?}",
                existing.pattern
            ));
        }
        let regex = Regex::new(&format!("^(?:{pattern})$"))
            .map_err(|e| format!("constraint {name:?}: {e}"))?;
        if self.custom.len() >= usize::from(u16::MAX) {
            return Err("too many constraints".into());
        }
        self.custom.push(Custom {
            name: name.to_string(),
            pattern: pattern.to_string(),
            regex,
        });
        Ok(())
    }

    fn lookup(&self, name: &str) -> Option<Constraint> {
        Constraint::parse(name).or_else(|| {
            let index = self.custom.iter().position(|c| c.name == name)?;
            u16::try_from(index).ok().map(Constraint::Custom)
        })
    }

    /// The regex of a custom constraint.
    pub fn regex(&self, constraint: Constraint) -> Option<&Regex> {
        match constraint {
            Constraint::Custom(index) => self.custom.get(usize::from(index)).map(|c| &c.regex),
            _ => None,
        }
    }

    /// Every custom constraint, as `(name, pattern)`.
    pub fn list(&self) -> Vec<(String, String)> {
        self.custom
            .iter()
            .map(|c| (c.name.clone(), c.pattern.clone()))
            .collect()
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
    #[cfg(test)]
    pub fn parse(raw: &'a str) -> Result<Self, PatternError> {
        Self::parse_with(raw, &Constraints::default())
    }

    /// Parses a segment; a constraint is a built-in one or one of `constraints`.
    pub fn parse_with(raw: &'a str, constraints: &Constraints) -> Result<Self, PatternError> {
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
                constraints
                    .lookup(c)
                    .ok_or_else(|| PatternError::UnknownConstraint(c.into()))?,
            ),
        };
        Ok(Self::Param { name, constraint })
    }
}

/// Why a route pattern was rejected.
#[derive(Debug, PartialEq, Eq)]
pub enum PatternError {
    /// `{id:float}`: not `int`, `uuid`, `str` or one the app defined.
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
                write!(
                    f,
                    "unknown constraint {c:?} (expected int, uuid, str, or one defined with constraint())"
                )
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
            let pattern = constraint.format("id", &Constraints::default());
            assert_eq!(
                Segment::parse(&pattern),
                Ok(Segment::Param {
                    name: "id",
                    constraint
                })
            );
        }
    }

    #[test]
    fn custom_constraints() {
        let mut constraints = Constraints::default();
        constraints
            .define("slug", "[a-z0-9]+(?:-[a-z0-9]+)*")
            .unwrap();
        let Ok(Segment::Param { constraint, .. }) =
            Segment::parse_with("{post:slug}", &constraints)
        else {
            panic!("not a parameter");
        };
        assert_eq!(constraint, Constraint::Custom(0));
        let regex = constraints.regex(constraint).unwrap();
        assert!(regex.is_match("hello-world") && !regex.is_match("Hello") && !regex.is_match("a-"));
        assert!(
            !regex.is_match("x hello-world"),
            "the whole value must match"
        );
        assert_eq!(constraint.format("post", &constraints), "{post:slug}");
        assert_eq!(
            constraints.list(),
            [("slug".to_string(), "[a-z0-9]+(?:-[a-z0-9]+)*".to_string())]
        );
    }

    #[test]
    fn defining_constraints() {
        let mut constraints = Constraints::default();
        assert!(constraints.define("hex", "[0-9a-f]+").is_ok());
        assert!(
            constraints.define("hex", "[0-9a-f]+").is_ok(),
            "the same again is fine"
        );
        assert!(
            constraints
                .define("hex", "[0-9A-F]+")
                .unwrap_err()
                .contains("already defined")
        );
        assert!(
            constraints
                .define("int", "x")
                .unwrap_err()
                .contains("built-in")
        );
        assert!(constraints.define("no way", "x").is_err());
        assert!(constraints.define("bad", "(").is_err());
        assert_eq!(
            Segment::parse_with("{id:nope}", &constraints),
            Err(PatternError::UnknownConstraint("nope".into()))
        );
    }
}
