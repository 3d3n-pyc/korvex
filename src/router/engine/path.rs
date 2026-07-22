//! Lazy, borrowing iteration over a path's `/`-delimited segments.

/// A lazy, borrowing iterator over a path's non-empty `/`-delimited
/// segments, e.g. `"/users/42/"` -> `"users"`, `"42"`. Leading, trailing,
/// and duplicate slashes are ignored.
///
/// Unlike a plain `str::split`, it also exposes [`PathSegments::remainder`]
/// — the contiguous, not-yet-consumed suffix of the original path — which
/// [`super::node::Node::find`] needs to capture a wildcard segment's value
/// as a single borrowed slice, without allocating.
#[derive(Debug, Clone)]
pub struct PathSegments<'a> {
    rest: &'a str,
}

impl<'a> PathSegments<'a> {
    pub fn new(path: &'a str) -> Self {
        Self {
            rest: path.trim_matches('/'),
        }
    }

    /// The remaining portion of the path, not yet consumed by [`next`](Iterator::next),
    /// with any leading slash trimmed. A wildcard capture is exactly this
    /// value — a borrowed, contiguous slice of the original path, so
    /// capturing it never allocates. Internal duplicate slashes (e.g. from
    /// `"a//b"`) are preserved as-is rather than collapsed, since doing so
    /// would require allocating a new string; malformed paths are expected
    /// to be normalized upstream (by the ASGI server, typically) before
    /// reaching the router.
    pub fn remainder(&self) -> &'a str {
        self.rest.trim_start_matches('/')
    }
}

impl<'a> Iterator for PathSegments<'a> {
    type Item = &'a str;

    fn next(&mut self) -> Option<&'a str> {
        loop {
            if self.rest.is_empty() {
                return None;
            }
            let (segment, tail) = self.rest.split_once('/').unwrap_or((self.rest, ""));
            self.rest = tail;
            if !segment.is_empty() {
                return Some(segment);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn yields_non_empty_segments_ignoring_duplicate_and_boundary_slashes() {
        assert_eq!(
            PathSegments::new("/users//42/").collect::<Vec<_>>(),
            vec!["users", "42"]
        );
        assert_eq!(
            PathSegments::new("users/42").collect::<Vec<_>>(),
            vec!["users", "42"]
        );
        assert_eq!(
            PathSegments::new("/").collect::<Vec<_>>(),
            Vec::<&str>::new()
        );
    }

    #[test]
    fn remainder_returns_contiguous_unconsumed_suffix() {
        let mut segments = PathSegments::new("static/a/b/c.png");
        assert_eq!(segments.remainder(), "static/a/b/c.png");
        segments.next();
        assert_eq!(segments.remainder(), "a/b/c.png");
    }

    #[test]
    fn remainder_is_empty_once_exhausted() {
        let mut segments = PathSegments::new("health");
        segments.next();
        assert_eq!(segments.remainder(), "");
    }
}
