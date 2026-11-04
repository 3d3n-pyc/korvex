//! The radix tree that stores routes and matches paths against them.
//!
//! Each node is one path segment. When matching, children are tried in
//! priority order: literals, then constrained parameters (`{id:int}`),
//! then the unconstrained parameter (`{name}`), then the wildcard. If a
//! branch fails further down, the next one is tried (backtracking).

use rustc_hash::FxHashMap;

use crate::method::Method;
use crate::path::{Constraint, PatternError, Segment, Segments};

/// What a route resolves to. Both are opaque names chosen by the caller.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Route {
    pub handler: String,
    pub middleware: Vec<String>,
}

/// Captured `(name, constraint, value)`, borrowed from the tree and the
/// path. The constraint tells the caller how to convert the value.
pub type Params<'a> = Vec<(&'a str, Constraint, &'a str)>;

/// The result of looking up a path for one method.
#[derive(Debug)]
pub enum Outcome<'a> {
    /// No route at this path.
    NotFound,
    /// Routes exist at this path, but none for this method (HTTP 405).
    /// Holds the routes that do exist, to report the allowed methods.
    MethodNotAllowed(&'a [(Method, Route)]),
    Matched(&'a Route),
}

impl Outcome<'_> {
    /// Combines the results of two failed branches: a 405 beats a 404, and
    /// the first 405 found wins.
    fn or(self, other: Self) -> Self {
        match self {
            Self::NotFound => other,
            _ => self,
        }
    }
}

/// The routes registered at one node, one per method. A `Vec` because a
/// node rarely has more than two methods, where a linear scan beats hashing.
#[derive(Debug, Default)]
struct Methods(Vec<(Method, Route)>);

impl Methods {
    fn insert(&mut self, method: Method, route: Route) {
        match self.0.iter_mut().find(|(m, _)| *m == method) {
            Some(entry) => entry.1 = route,
            None => self.0.push((method, route)),
        }
    }

    fn remove(&mut self, method: Method) -> bool {
        let before = self.0.len();
        self.0.retain(|(m, _)| *m != method);
        self.0.len() != before
    }

    fn lookup(&self, method: Method) -> Outcome<'_> {
        if self.0.is_empty() {
            return Outcome::NotFound;
        }
        match self.0.iter().find(|(m, _)| *m == method) {
            Some((_, route)) => Outcome::Matched(route),
            None => Outcome::MethodNotAllowed(&self.0),
        }
    }
}

#[derive(Debug)]
struct Param {
    name: String,
    constraint: Constraint,
    node: Node,
}

/// A wildcard has no children: it always consumes the rest of the path.
#[derive(Debug)]
struct Wildcard {
    name: String,
    methods: Methods,
}

#[derive(Debug, Default)]
pub struct Node {
    /// Routes that end exactly here.
    methods: Methods,
    literals: FxHashMap<String, Node>,
    /// At most one per constraint, in matching order: constrained first,
    /// `Constraint::Any` last.
    params: Vec<Param>,
    wildcard: Option<Wildcard>,
}

impl Node {
    /// Registers `route` for `pattern` (e.g. `/users/{id:int}`) and
    /// `method`, replacing any route already there.
    pub fn insert(
        &mut self,
        pattern: &str,
        method: Method,
        route: Route,
    ) -> Result<(), PatternError> {
        let segments: Vec<&str> = Segments::new(pattern).collect();
        self.insert_at(&segments, method, route)
    }

    fn insert_at(
        &mut self,
        segments: &[&str],
        method: Method,
        route: Route,
    ) -> Result<(), PatternError> {
        let Some((first, rest)) = segments.split_first() else {
            self.methods.insert(method, route);
            return Ok(());
        };
        match Segment::parse(first)? {
            Segment::Literal(literal) => self
                .literals
                .entry(literal.to_string())
                .or_default()
                .insert_at(rest, method, route),
            Segment::Param { name, constraint } => {
                let index = match self.params.iter().position(|p| p.constraint == constraint) {
                    Some(i) if self.params[i].name == name => i,
                    Some(i) => return Err(conflict(&self.params[i].name, name)),
                    None => {
                        let i = if constraint == Constraint::Any {
                            self.params.len()
                        } else {
                            0
                        };
                        self.params.insert(
                            i,
                            Param {
                                name: name.to_string(),
                                constraint,
                                node: Node::default(),
                            },
                        );
                        i
                    }
                };
                self.params[index].node.insert_at(rest, method, route)
            }
            Segment::Wildcard(name) => {
                if !rest.is_empty() {
                    return Err(PatternError::WildcardNotLast);
                }
                let wildcard = self.wildcard.get_or_insert_with(|| Wildcard {
                    name: name.to_string(),
                    methods: Methods::default(),
                });
                if wildcard.name != name {
                    return Err(conflict(&wildcard.name, name));
                }
                wildcard.methods.insert(method, route);
                Ok(())
            }
        }
    }

    /// Removes the route registered for exactly `pattern` and `method`.
    /// Returns whether there was one. Emptied nodes are left in place:
    /// they just stop matching.
    pub fn remove(&mut self, pattern: &str, method: Method) -> Result<bool, PatternError> {
        let segments: Vec<&str> = Segments::new(pattern).collect();
        self.remove_at(&segments, method)
    }

    fn remove_at(&mut self, segments: &[&str], method: Method) -> Result<bool, PatternError> {
        let Some((first, rest)) = segments.split_first() else {
            return Ok(self.methods.remove(method));
        };
        Ok(match Segment::parse(first)? {
            Segment::Literal(literal) => match self.literals.get_mut(literal) {
                Some(child) => child.remove_at(rest, method)?,
                None => false,
            },
            Segment::Param { name, constraint } => {
                match self
                    .params
                    .iter_mut()
                    .find(|p| p.constraint == constraint && p.name == name)
                {
                    Some(param) => param.node.remove_at(rest, method)?,
                    None => false,
                }
            }
            Segment::Wildcard(name) => match &mut self.wildcard {
                Some(wildcard) if wildcard.name == name => wildcard.methods.remove(method),
                _ => false,
            },
        })
    }

    /// Matches `path` for `method`, pushing captured parameters onto
    /// `params`. Allocates nothing besides `params` itself.
    pub fn find<'a>(
        &'a self,
        path: &'a str,
        method: Method,
        params: &mut Params<'a>,
    ) -> Outcome<'a> {
        self.find_from(Segments::new(path), method, params)
    }

    fn find_from<'a>(
        &'a self,
        mut segments: Segments<'a>,
        method: Method,
        params: &mut Params<'a>,
    ) -> Outcome<'a> {
        let rest_of_path = segments.remainder();
        let Some(segment) = segments.next() else {
            return self.methods.lookup(method);
        };

        // Best failure so far, returned if no branch matches.
        let mut best = Outcome::NotFound;

        if let Some(child) = self.literals.get(segment) {
            match child.find_from(segments.clone(), method, params) {
                found @ Outcome::Matched(_) => return found,
                failed => best = best.or(failed),
            }
        }

        for param in &self.params {
            if !param.constraint.matches(segment) {
                continue;
            }
            params.push((param.name.as_str(), param.constraint, segment));
            match param.node.find_from(segments.clone(), method, params) {
                found @ Outcome::Matched(_) => return found,
                failed => best = best.or(failed),
            }
            params.pop();
        }

        if let Some(wildcard) = &self.wildcard {
            params.push((wildcard.name.as_str(), Constraint::Any, rest_of_path));
            match wildcard.methods.lookup(method) {
                found @ Outcome::Matched(_) => return found,
                failed => best = best.or(failed),
            }
            params.pop();
        }

        best
    }

    /// Every registered route as `(pattern, method, route)`, in no
    /// particular order. For introspection, not used when matching.
    pub fn routes(&self) -> Vec<(String, Method, Route)> {
        let mut out = Vec::new();
        self.collect_routes("", &mut out);
        out
    }

    fn collect_routes(&self, prefix: &str, out: &mut Vec<(String, Method, Route)>) {
        let here = if prefix.is_empty() { "/" } else { prefix };
        for (method, route) in &self.methods.0 {
            out.push((here.to_string(), *method, route.clone()));
        }
        for (literal, child) in &self.literals {
            child.collect_routes(&format!("{prefix}/{literal}"), out);
        }
        for param in &self.params {
            let segment = param.constraint.format(&param.name);
            param
                .node
                .collect_routes(&format!("{prefix}/{segment}"), out);
        }
        if let Some(wildcard) = &self.wildcard {
            let pattern = format!("{prefix}/*{}", wildcard.name);
            for (method, route) in &wildcard.methods.0 {
                out.push((pattern.clone(), *method, route.clone()));
            }
        }
    }
}

fn conflict(existing: &str, new: &str) -> PatternError {
    PatternError::ConflictingNames {
        existing: existing.to_string(),
        new: new.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use Method::{Delete, Get, Post};

    fn route(handler: &str) -> Route {
        Route {
            handler: handler.to_string(),
            middleware: Vec::new(),
        }
    }

    fn tree(routes: &[(&str, Method, &str)]) -> Node {
        let mut root = Node::default();
        for (pattern, method, handler) in routes {
            root.insert(pattern, *method, route(handler)).unwrap();
        }
        root
    }

    /// Summarizes a lookup as `"handler"`, `"404"` or `"405 GET,POST"`,
    /// plus the captured params.
    fn lookup<'a>(
        root: &'a Node,
        method: Method,
        path: &'a str,
    ) -> (String, Vec<(&'a str, &'a str)>) {
        let mut params = Vec::new();
        let summary = match root.find(path, method, &mut params) {
            Outcome::Matched(route) => route.handler.clone(),
            Outcome::NotFound => "404".to_string(),
            Outcome::MethodNotAllowed(allowed) => {
                let names: Vec<_> = allowed.iter().map(|(m, _)| m.as_str()).collect();
                format!("405 {}", names.join(","))
            }
        };
        let params = params
            .into_iter()
            .map(|(name, _, value)| (name, value))
            .collect();
        (summary, params)
    }

    fn handler(root: &Node, method: Method, path: &str) -> String {
        lookup(root, method, path).0
    }

    #[test]
    fn matches_literal_and_root_paths() {
        let root = tree(&[("/", Get, "root"), ("/health", Get, "health")]);
        assert_eq!(lookup(&root, Get, "/"), ("root".into(), vec![]));
        assert_eq!(lookup(&root, Get, "/health"), ("health".into(), vec![]));
        assert_eq!(handler(&root, Get, "/unknown"), "404");
    }

    #[test]
    fn captures_params() {
        let root = tree(&[("/users/{id}/posts/{post}", Get, "post")]);
        assert_eq!(
            lookup(&root, Get, "/users/42/posts/7"),
            ("post".into(), vec![("id", "42"), ("post", "7")])
        );
    }

    #[test]
    fn literal_beats_param() {
        let root = tree(&[("/users/{id}", Get, "user"), ("/users/me", Get, "me")]);
        assert_eq!(lookup(&root, Get, "/users/me"), ("me".into(), vec![]));
        assert_eq!(handler(&root, Get, "/users/42"), "user");
    }

    #[test]
    fn failed_branch_leaves_no_params_behind() {
        let root = tree(&[("/users/{id}/profile", Get, "profile")]);
        assert_eq!(
            lookup(&root, Get, "/users/42/settings"),
            ("404".into(), vec![])
        );
    }

    #[test]
    fn backtracks_from_literal_to_param() {
        let root = tree(&[
            ("/files/new/edit", Get, "edit_new"),
            ("/files/{id}", Get, "file"),
        ]);
        assert_eq!(
            lookup(&root, Get, "/files/new"),
            ("file".into(), vec![("id", "new")])
        );
    }

    #[test]
    fn constrained_param_is_tried_before_unconstrained() {
        // Registered unconstrained first, to show order doesn't matter.
        let root = tree(&[
            ("/users/{slug}", Get, "by_slug"),
            ("/users/{id:int}", Get, "by_id"),
        ]);
        assert_eq!(
            lookup(&root, Get, "/users/42"),
            ("by_id".into(), vec![("id", "42")])
        );
        assert_eq!(
            lookup(&root, Get, "/users/abc"),
            ("by_slug".into(), vec![("slug", "abc")])
        );
    }

    #[test]
    fn reports_each_capture_constraint() {
        use Constraint::{Any, Int, Uuid};
        let root = tree(&[("/u/{id:int}/{key:uuid}/{name}/*rest", Get, "x")]);
        let mut params = Vec::new();
        let path = "/u/42/123e4567-e89b-12d3-a456-426614174000/ada/a/b";
        assert!(matches!(
            root.find(path, Get, &mut params),
            Outcome::Matched(_)
        ));
        let constraints: Vec<_> = params.iter().map(|(name, c, _)| (*name, *c)).collect();
        assert_eq!(
            constraints,
            [("id", Int), ("key", Uuid), ("name", Any), ("rest", Any)]
        );
    }

    #[test]
    fn constraint_mismatch_is_not_found() {
        let root = tree(&[("/users/{id:int}", Get, "user")]);
        assert_eq!(lookup(&root, Get, "/users/abc"), ("404".into(), vec![]));
    }

    #[test]
    fn same_param_can_be_shared_by_several_routes() {
        let root = tree(&[
            ("/users/{id:int}/profile", Get, "profile"),
            ("/users/{id:int}/posts", Get, "posts"),
        ]);
        assert_eq!(handler(&root, Get, "/users/42/profile"), "profile");
        assert_eq!(handler(&root, Get, "/users/42/posts"), "posts");
    }

    #[test]
    fn wildcard_captures_the_rest_of_the_path() {
        let root = tree(&[("/static/*path", Get, "static")]);
        assert_eq!(
            lookup(&root, Get, "/static/css/app.css"),
            ("static".into(), vec![("path", "css/app.css")])
        );
        assert_eq!(
            handler(&root, Get, "/static"),
            "404",
            "needs at least one segment"
        );
    }

    #[test]
    fn wildcard_comes_last() {
        let root = tree(&[
            ("/files/*rest", Get, "wildcard"),
            ("/files/readme", Get, "literal"),
            ("/files/{name}", Get, "param"),
        ]);
        assert_eq!(handler(&root, Get, "/files/readme"), "literal");
        assert_eq!(handler(&root, Get, "/files/other"), "param");
        assert_eq!(handler(&root, Get, "/files/a/b"), "wildcard");
    }

    #[test]
    fn methods_are_independent() {
        let root = tree(&[("/users", Get, "list"), ("/users", Post, "create")]);
        assert_eq!(handler(&root, Get, "/users"), "list");
        assert_eq!(handler(&root, Post, "/users"), "create");
        assert_eq!(handler(&root, Delete, "/users"), "405 GET,POST");
    }

    #[test]
    fn method_not_allowed_wins_over_not_found_in_another_branch() {
        let root = tree(&[("/a/{x}", Post, "post_x"), ("/a/b/c", Get, "abc")]);
        assert_eq!(handler(&root, Get, "/a/b"), "405 POST");
    }

    #[test]
    fn registering_again_replaces_the_route() {
        let root = tree(&[("/users", Get, "v1"), ("/users", Get, "v2")]);
        assert_eq!(handler(&root, Get, "/users"), "v2");
    }

    #[test]
    fn keeps_middleware() {
        let mut root = Node::default();
        let with_middleware = Route {
            handler: "list".into(),
            middleware: vec!["auth".into(), "log".into()],
        };
        root.insert("/users", Get, with_middleware.clone()).unwrap();
        let mut params = Vec::new();
        assert!(
            matches!(root.find("/users", Get, &mut params), Outcome::Matched(r) if *r == with_middleware)
        );
    }

    #[test]
    fn rejects_invalid_patterns() {
        let mut root = tree(&[
            ("/users/{id}", Get, "user"),
            ("/static/*path", Get, "static"),
        ]);
        let mut insert = |pattern| root.insert(pattern, Get, route("x")).unwrap_err();
        assert_eq!(insert("/users/{slug}"), conflict("id", "slug"));
        assert_eq!(insert("/static/*file"), conflict("path", "file"));
        assert_eq!(insert("/a/*path/more"), PatternError::WildcardNotLast);
        assert_eq!(insert("/a/*"), PatternError::UnnamedWildcard);
        assert_eq!(
            insert("/a/{id:float}"),
            PatternError::UnknownConstraint("float".into())
        );
    }

    #[test]
    fn removes_routes() {
        let mut root = tree(&[
            ("/users", Get, "list"),
            ("/users", Post, "create"),
            ("/users/{id:int}", Get, "user"),
            ("/static/*path", Get, "static"),
        ]);

        assert_eq!(root.remove("/users", Get), Ok(true));
        assert_eq!(root.remove("/users", Get), Ok(false), "already removed");
        assert_eq!(handler(&root, Get, "/users"), "405 POST");
        assert_eq!(handler(&root, Post, "/users"), "create");

        assert_eq!(
            root.remove("/users/{id}", Get),
            Ok(false),
            "wrong constraint"
        );
        assert_eq!(handler(&root, Get, "/users/42"), "user");

        assert_eq!(root.remove("/static/*path", Get), Ok(true));
        assert_eq!(handler(&root, Get, "/static/a"), "404");

        assert_eq!(
            root.remove("/users/{id:float}", Get),
            Err(PatternError::UnknownConstraint("float".into()))
        );
    }

    #[test]
    fn lists_routes_in_pattern_syntax() {
        let mut root = tree(&[
            ("/", Get, "root"),
            ("/users/{id:int}", Get, "user"),
            ("/users", Post, "create"),
            ("/static/*path", Get, "static"),
        ]);
        root.remove("/", Get).unwrap();

        let mut routes: Vec<_> = root
            .routes()
            .into_iter()
            .map(|(p, m, r)| (p, m, r.handler))
            .collect();
        routes.sort();
        let expected = [
            ("/static/*path", Get, "static"),
            ("/users", Post, "create"),
            ("/users/{id:int}", Get, "user"),
        ];
        assert_eq!(
            routes,
            expected.map(|(p, m, h)| (p.to_string(), m, h.to_string()))
        );
    }
}
