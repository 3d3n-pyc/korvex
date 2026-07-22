//! Parsing of a single route-path segment into its structural meaning:
//! a literal string, or a named dynamic segment optionally restricted by
//! a [`Constraint`]. Parsing happens once, at route registration — the
//! matching hot path never re-parses a segment, only walks the tree
//! [`ParsedSegment`] already shaped at insert time.

use std::fmt;

/// A constraint restricting what a dynamic segment's captured value may
/// be. A closed enum, not `Box<dyn Constraint>`: the built-in set is
/// small and known, so [`Constraint::matches`] stays a plain branch
/// (monomorphized) rather than a vtable dispatch on the matching path.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Constraint {
    /// No restriction — matches any non-empty segment. Also the
    /// constraint for a bare `{name}` or an explicit `{name:str}`.
    Any,
    /// The segment must parse as a signed integer.
    Int,
    /// The segment must look like a UUID (8-4-4-4-12 hex digits).
    Uuid,
}

impl Constraint {
    fn parse(name: &str) -> Option<Self> {
        Some(match name {
            "str" => Self::Any,
            "int" => Self::Int,
            "uuid" => Self::Uuid,
            _ => return None,
        })
    }

    pub fn matches(self, value: &str) -> bool {
        match self {
            Self::Any => true,
            Self::Int => value.parse::<i64>().is_ok(),
            Self::Uuid => is_uuid(value),
        }
    }

    /// The `:constraint` suffix this constraint round-trips to when
    /// reconstructing a path pattern for introspection (`Node::routes`).
    /// `None` for `Any`, since a bare `{name}` (rather than the equally
    /// valid but noisier `{name:str}`) is the canonical unconstrained form.
    pub fn suffix(self) -> Option<&'static str> {
        match self {
            Self::Any => None,
            Self::Int => Some("int"),
            Self::Uuid => Some("uuid"),
        }
    }
}

/// Checks the `8-4-4-4-12` hex-digit shape of a UUID without pulling in a
/// dependency for it.
fn is_uuid(value: &str) -> bool {
    let group_lengths = [8, 4, 4, 4, 12];
    let mut groups = value.split('-');
    group_lengths.into_iter().all(|len| {
        groups
            .next()
            .is_some_and(|group| group.len() == len && group.bytes().all(|b| b.is_ascii_hexdigit()))
    }) && groups.next().is_none()
}

/// The structural meaning of one `/`-delimited path segment.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParsedSegment<'a> {
    /// A literal segment, e.g. `users`.
    Static(&'a str),
    /// A named dynamic segment, e.g. `{id}` or `{id:int}`.
    Param {
        name: &'a str,
        constraint: Constraint,
    },
    /// A catch-all segment, e.g. `*filepath`, capturing the rest of the
    /// path. Only valid as the last segment of a route.
    Wildcard(&'a str),
}

/// Why a route registration was rejected.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InsertError {
    /// `{name:constraint}` named a constraint Korvex doesn't know.
    UnknownConstraint(String),
    /// Two routes register a dynamic or wildcard segment at the same
    /// depth, under the same constraint, but with different names —
    /// ambiguous, since a single match can only report one name for that
    /// position.
    ConflictingParamName { existing: String, new: String },
    /// `*` with nothing after it — a wildcard segment must be named.
    EmptyWildcardName,
    /// `*name` was followed by more segments; a wildcard consumes the
    /// rest of the path, so nothing can be registered underneath it.
    WildcardMustBeLastSegment,
}

impl fmt::Display for InsertError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnknownConstraint(name) => write!(f, "unknown route param constraint: {name:?}"),
            Self::ConflictingParamName { existing, new } => {
                write!(
                    f,
                    "conflicting param names at the same path depth: {existing:?} vs {new:?}"
                )
            }
            Self::EmptyWildcardName => {
                write!(f, "wildcard segment `*` must be named, e.g. `*filepath`")
            }
            Self::WildcardMustBeLastSegment => {
                write!(f, "a wildcard segment must be the last segment of a route")
            }
        }
    }
}

impl std::error::Error for InsertError {}

/// Parses one raw path segment, e.g. `"{id:int}"` -> `Param { name: "id", constraint: Int }`,
/// `"*filepath"` -> `Wildcard("filepath")`, `"users"` -> `Static("users")`.
pub fn parse_segment(segment: &str) -> Result<ParsedSegment<'_>, InsertError> {
    if let Some(name) = segment.strip_prefix('*') {
        return if name.is_empty() {
            Err(InsertError::EmptyWildcardName)
        } else {
            Ok(ParsedSegment::Wildcard(name))
        };
    }

    let Some(inner) = segment.strip_prefix('{').and_then(|s| s.strip_suffix('}')) else {
        return Ok(ParsedSegment::Static(segment));
    };
    match inner.split_once(':') {
        Some((name, constraint_name)) => {
            let constraint = Constraint::parse(constraint_name)
                .ok_or_else(|| InsertError::UnknownConstraint(constraint_name.to_string()))?;
            Ok(ParsedSegment::Param { name, constraint })
        }
        None => Ok(ParsedSegment::Param {
            name: inner,
            constraint: Constraint::Any,
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_static_segment() {
        assert_eq!(parse_segment("users"), Ok(ParsedSegment::Static("users")));
    }

    #[test]
    fn parses_unconstrained_param() {
        assert_eq!(
            parse_segment("{id}"),
            Ok(ParsedSegment::Param {
                name: "id",
                constraint: Constraint::Any
            })
        );
    }

    #[test]
    fn parses_int_constraint() {
        assert_eq!(
            parse_segment("{id:int}"),
            Ok(ParsedSegment::Param {
                name: "id",
                constraint: Constraint::Int
            })
        );
    }

    #[test]
    fn parses_uuid_constraint() {
        assert_eq!(
            parse_segment("{id:uuid}"),
            Ok(ParsedSegment::Param {
                name: "id",
                constraint: Constraint::Uuid
            })
        );
    }

    #[test]
    fn str_constraint_is_an_alias_for_any() {
        assert_eq!(
            parse_segment("{id:str}"),
            Ok(ParsedSegment::Param {
                name: "id",
                constraint: Constraint::Any
            })
        );
    }

    #[test]
    fn rejects_unknown_constraint() {
        assert_eq!(
            parse_segment("{id:float}"),
            Err(InsertError::UnknownConstraint("float".to_string()))
        );
    }

    #[test]
    fn int_constraint_matches_only_integers() {
        assert!(Constraint::Int.matches("42"));
        assert!(Constraint::Int.matches("-7"));
        assert!(!Constraint::Int.matches("abc"));
        assert!(!Constraint::Int.matches("4.2"));
        assert!(!Constraint::Int.matches(""));
    }

    #[test]
    fn uuid_constraint_matches_only_well_formed_uuids() {
        assert!(Constraint::Uuid.matches("123e4567-e89b-12d3-a456-426614174000"));
        assert!(!Constraint::Uuid.matches("not-a-uuid"));
        assert!(!Constraint::Uuid.matches("123e4567-e89b-12d3-a456"));
    }

    #[test]
    fn any_constraint_matches_any_non_empty_value() {
        assert!(Constraint::Any.matches("anything"));
        assert!(Constraint::Any.matches("42"));
    }

    #[test]
    fn suffix_round_trips_through_parse() {
        for constraint in [Constraint::Int, Constraint::Uuid] {
            assert_eq!(
                Constraint::parse(constraint.suffix().unwrap()),
                Some(constraint)
            );
        }
        assert_eq!(Constraint::Any.suffix(), None);
    }

    #[test]
    fn parses_wildcard_segment() {
        assert_eq!(
            parse_segment("*filepath"),
            Ok(ParsedSegment::Wildcard("filepath"))
        );
    }

    #[test]
    fn rejects_empty_wildcard_name() {
        assert_eq!(parse_segment("*"), Err(InsertError::EmptyWildcardName));
    }
}
