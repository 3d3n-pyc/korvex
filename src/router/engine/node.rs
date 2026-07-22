//! Radix-tree node implementation for route matching.
//!
//! This module contains the pure Rust logic for inserting and matching
//! routes, with no `PyO3` dependency — it can be tested and reasoned about
//! independently of the Python bindings.

use rustc_hash::FxHashMap;

use super::handler::{MatchOutcome, MethodTable, RouteHandler};
use super::method::Method;
use super::path::PathSegments;
use super::segment::{Constraint, InsertError, ParsedSegment, parse_segment};

/// A dynamic (`{name}` or `{name:constraint}`) child of a [`Node`].
#[derive(Debug)]
struct ParamChild {
    name: String,
    constraint: Constraint,
    node: Box<Node>,
}

/// A catch-all (`*name`) child of a [`Node`]. Deliberately has no nested
/// [`Node`] — a wildcard consumes all remaining segments, so nothing can
/// be registered underneath it; [`Node::insert`] enforces this
/// structurally by rejecting a wildcard that isn't a route's last segment.
#[derive(Debug)]
struct WildcardChild {
    name: String,
    methods: MethodTable,
}

/// A single node in the route radix tree.
///
/// Each node represents one path segment. A node can have any number of
/// static children (exact string matches), any number of dynamic children
/// (at most one per distinct [`Constraint`] — two `{name:int}` children at
/// the same depth would be ambiguous, but `{id:int}` and `{slug}` can
/// coexist), and at most one wildcard child.
#[derive(Default, Debug)]
pub struct Node {
    static_children: FxHashMap<String, Node>,
    param_children: Vec<ParamChild>,
    wildcard: Option<WildcardChild>,
    methods: MethodTable,
}

impl Node {
    /// Inserts a route into the tree.
    ///
    /// `segments` is the path already split on `/`, e.g. `["users", "{id}"]`.
    /// `handler` is registered under `method`; registering the same
    /// `(path, method)` again replaces the previous handler. Fails if a
    /// segment names an unknown constraint, is an unnamed or non-final
    /// wildcard, or conflicts with an already-registered dynamic or
    /// wildcard segment at the same depth (see [`InsertError`]).
    pub fn insert(
        &mut self,
        segments: &[&str],
        method: Method,
        handler: RouteHandler,
    ) -> Result<(), InsertError> {
        let Some((seg, rest)) = segments.split_first() else {
            self.methods.insert(method, handler);
            return Ok(());
        };

        match parse_segment(seg)? {
            ParsedSegment::Static(literal) => {
                let child = self.static_children.entry(literal.to_string()).or_default();
                child.insert(rest, method, handler)
            }
            ParsedSegment::Param { name, constraint } => {
                let child = match self
                    .param_children
                    .iter_mut()
                    .find(|c| c.constraint == constraint)
                {
                    Some(child) if child.name == name => child,
                    Some(child) => {
                        return Err(InsertError::ConflictingParamName {
                            existing: child.name.clone(),
                            new: name.to_string(),
                        });
                    }
                    None => {
                        self.param_children.push(ParamChild {
                            name: name.to_string(),
                            constraint,
                            node: Box::default(),
                        });
                        self.param_children.last_mut().expect("just pushed")
                    }
                };
                child.node.insert(rest, method, handler)
            }
            ParsedSegment::Wildcard(name) => {
                if !rest.is_empty() {
                    return Err(InsertError::WildcardMustBeLastSegment);
                }
                let wildcard = self.wildcard.get_or_insert_with(|| WildcardChild {
                    name: name.to_string(),
                    methods: MethodTable::default(),
                });
                if wildcard.name != name {
                    return Err(InsertError::ConflictingParamName {
                        existing: wildcard.name.clone(),
                        new: name.to_string(),
                    });
                }
                wildcard.methods.insert(method, handler);
                Ok(())
            }
        }
    }

    /// Removes the route registered at exactly `segments` (the same
    /// syntax `insert` takes — a path pattern, not an example URL to
    /// match) for `method`. Returns whether a route was actually removed;
    /// removing something never registered is not an error, only a
    /// malformed constraint name is (mirroring `insert`).
    ///
    /// Does not prune now-empty nodes from the tree afterward — matching
    /// correctness only depends on [`Node::find`] seeing an empty method
    /// table, not on the node itself being absent, so a removed route
    /// simply stops matching without needing a second, more invasive
    /// tree-shaping pass. This does mean memory from removed routes isn't
    /// reclaimed; fine for the common case (routes configured once at
    /// startup), worth revisiting if a workload does heavy add/remove churn.
    pub fn remove(&mut self, segments: &[&str], method: Method) -> Result<bool, InsertError> {
        let Some((seg, rest)) = segments.split_first() else {
            return Ok(self.methods.remove(method));
        };

        match parse_segment(seg)? {
            ParsedSegment::Static(literal) => match self.static_children.get_mut(literal) {
                Some(child) => child.remove(rest, method),
                None => Ok(false),
            },
            ParsedSegment::Param { name, constraint } => {
                match self
                    .param_children
                    .iter_mut()
                    .find(|c| c.constraint == constraint && c.name == name)
                {
                    Some(child) => child.node.remove(rest, method),
                    None => Ok(false),
                }
            }
            ParsedSegment::Wildcard(name) => match &mut self.wildcard {
                Some(wildcard) if wildcard.name == name => Ok(wildcard.methods.remove(method)),
                _ => Ok(false),
            },
        }
    }

    /// Every registered route as `(path, method, handler)`, path
    /// reconstructed in canonical form (e.g. `{id:int}`, `*filepath`).
    /// For introspection — debugging, generating docs, an admin view —
    /// never called from the matching hot path, so this allocates and
    /// walks the whole tree freely. Order is unspecified (children are
    /// visited via a hash map internally); callers that want a stable
    /// order should sort the result themselves.
    pub fn routes(&self) -> Vec<(String, Method, RouteHandler)> {
        let mut out = Vec::new();
        self.collect_routes(&mut String::new(), &mut out);
        out
    }

    fn collect_routes(&self, path: &mut String, out: &mut Vec<(String, Method, RouteHandler)>) {
        let route_path = || {
            if path.is_empty() {
                "/".to_string()
            } else {
                path.clone()
            }
        };
        out.extend(
            self.methods
                .entries()
                .map(|(method, handler)| (route_path(), method, handler.clone())),
        );

        for (segment, child) in &self.static_children {
            let len = path.len();
            path.push('/');
            path.push_str(segment);
            child.collect_routes(path, out);
            path.truncate(len);
        }

        for param in &self.param_children {
            let len = path.len();
            path.push_str("/{");
            path.push_str(&param.name);
            if let Some(suffix) = param.constraint.suffix() {
                path.push(':');
                path.push_str(suffix);
            }
            path.push('}');
            param.node.collect_routes(path, out);
            path.truncate(len);
        }

        if let Some(wildcard) = &self.wildcard {
            let len = path.len();
            path.push_str("/*");
            path.push_str(&wildcard.name);
            out.extend(
                wildcard
                    .methods
                    .entries()
                    .map(|(method, handler)| (path.clone(), method, handler.clone())),
            );
            path.truncate(len);
        }
    }

    /// Attempts to match a path (as its segment iterator) against this
    /// subtree for the given method.
    ///
    /// `segments` is consumed lazily rather than pre-collected into a
    /// `Vec`, so a lookup performs no segment-list allocation; branches
    /// that fail replay a clone of it down the next candidate.
    ///
    /// Priority order: static segments first (`/users/me` before
    /// `/users/{id}`), then dynamic segments — constrained ones
    /// (`{id:int}`) before the unconstrained fallback (`{name}`), in a
    /// fixed order independent of registration order, with a
    /// constraint-failing segment simply skipped rather than failing the
    /// whole match — then a wildcard (`*name`) last, if present, capturing
    /// every remaining segment as one borrowed slice via
    /// [`PathSegments::remainder`]. A node with no wildcard child pays only
    /// an `Option::is_some` check for this — no cost for the common case.
    ///
    /// Matched parameter values are appended to `params` as `(name, value)`
    /// pairs; on a failed branch they are popped back off, so `params`
    /// reflects only the successful path on return. Both the name (stored
    /// in the tree) and the value (a slice of the looked-up path) are
    /// borrowed for `'a` rather than cloned — matching allocates nothing;
    /// only the caller, at the FFI boundary, needs to turn these into
    /// owned `String`s for Python.
    ///
    /// A path that matches structurally but not for `method` yields
    /// [`MatchOutcome::MethodNotAllowed`] rather than [`MatchOutcome::NotFound`]
    /// — the first such candidate found wins; sibling branches are not
    /// merged into one combined allowed-methods list, since two different
    /// branches producing a 405 at the same depth is a rare edge case not
    /// worth an allocation to reconcile.
    pub fn find<'a>(
        &'a self,
        mut segments: PathSegments<'a>,
        method: Method,
        params: &mut Vec<(&'a str, &'a str)>,
    ) -> MatchOutcome<'a> {
        let wildcard_capture = self.wildcard.is_some().then(|| segments.remainder());

        let Some(seg) = segments.next() else {
            return self.methods.outcome(method);
        };

        let mut candidate = MatchOutcome::NotFound;

        if let Some(child) = self.static_children.get(seg) {
            match child.find(segments.clone(), method, params) {
                MatchOutcome::Matched(handler) => return MatchOutcome::Matched(handler),
                outcome @ MatchOutcome::MethodNotAllowed(_) => candidate = outcome,
                MatchOutcome::NotFound => {}
            }
        }

        let constrained = self
            .param_children
            .iter()
            .filter(|c| c.constraint != Constraint::Any);
        let unconstrained = self
            .param_children
            .iter()
            .filter(|c| c.constraint == Constraint::Any);
        for child in constrained.chain(unconstrained) {
            if !child.constraint.matches(seg) {
                continue;
            }
            params.push((child.name.as_str(), seg));
            match child.node.find(segments.clone(), method, params) {
                MatchOutcome::Matched(handler) => return MatchOutcome::Matched(handler),
                outcome @ MatchOutcome::MethodNotAllowed(_) => {
                    params.pop();
                    if matches!(candidate, MatchOutcome::NotFound) {
                        candidate = outcome;
                    }
                }
                MatchOutcome::NotFound => {
                    params.pop();
                }
            }
        }

        if let Some(wildcard) = &self.wildcard {
            let value =
                wildcard_capture.expect("wildcard_capture is set whenever self.wildcard is Some");
            if !value.is_empty() {
                params.push((wildcard.name.as_str(), value));
                match wildcard.methods.outcome(method) {
                    MatchOutcome::Matched(handler) => return MatchOutcome::Matched(handler),
                    outcome @ MatchOutcome::MethodNotAllowed(_) => {
                        params.pop();
                        if matches!(candidate, MatchOutcome::NotFound) {
                            candidate = outcome;
                        }
                    }
                    MatchOutcome::NotFound => {
                        params.pop();
                    }
                }
            }
        }

        candidate
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn handler(name: &str) -> RouteHandler {
        RouteHandler {
            name: name.to_string(),
            middleware: Vec::new(),
        }
    }

    fn find<'a>(
        root: &'a Node,
        path: &'a str,
        method: Method,
        params: &mut Vec<(&'a str, &'a str)>,
    ) -> MatchOutcome<'a> {
        root.find(PathSegments::new(path), method, params)
    }

    #[test]
    fn matches_static_route() {
        let mut root = Node::default();
        root.insert(&["health"], Method::Get, handler("health_check"))
            .unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "health", Method::Get, &mut params);
        assert!(matches!(outcome, MatchOutcome::Matched(h) if h.name == "health_check"));
        assert!(params.is_empty());
    }

    #[test]
    fn matches_root_path() {
        let mut root = Node::default();
        root.insert(&[], Method::Get, handler("root")).unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "", Method::Get, &mut params);
        assert!(matches!(outcome, MatchOutcome::Matched(h) if h.name == "root"));
        assert!(params.is_empty());
    }

    #[test]
    fn matches_dynamic_segment_and_captures_param() {
        let mut root = Node::default();
        root.insert(&["users", "{id}"], Method::Get, handler("get_user"))
            .unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "users/42", Method::Get, &mut params);

        assert!(matches!(outcome, MatchOutcome::Matched(h) if h.name == "get_user"));
        assert_eq!(params, vec![("id", "42")]);
    }

    #[test]
    fn prefers_static_over_dynamic_at_same_level() {
        let mut root = Node::default();
        root.insert(&["users", "{id}"], Method::Get, handler("get_user"))
            .unwrap();
        root.insert(&["users", "me"], Method::Get, handler("get_current_user"))
            .unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "users/me", Method::Get, &mut params);

        assert!(matches!(outcome, MatchOutcome::Matched(h) if h.name == "get_current_user"));
        assert!(params.is_empty(), "static match should not capture params");
    }

    #[test]
    fn returns_not_found_for_unknown_route() {
        let mut root = Node::default();
        root.insert(&["health"], Method::Get, handler("health_check"))
            .unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "unknown", Method::Get, &mut params);
        assert!(matches!(outcome, MatchOutcome::NotFound));
    }

    #[test]
    fn backtracks_params_on_failed_branch() {
        let mut root = Node::default();
        root.insert(
            &["users", "{id}", "profile"],
            Method::Get,
            handler("get_profile"),
        )
        .unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "users/42/settings", Method::Get, &mut params);

        assert!(matches!(outcome, MatchOutcome::NotFound));
        assert!(params.is_empty());
    }

    #[test]
    fn matches_only_registered_method() {
        let mut root = Node::default();
        root.insert(&["users"], Method::Get, handler("list_users"))
            .unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "users", Method::Get, &mut params);
        assert!(matches!(outcome, MatchOutcome::Matched(h) if h.name == "list_users"));
    }

    #[test]
    fn returns_method_not_allowed_with_allowed_methods() {
        let mut root = Node::default();
        root.insert(&["users"], Method::Get, handler("list_users"))
            .unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "users", Method::Post, &mut params);

        match outcome {
            MatchOutcome::MethodNotAllowed(allowed) => {
                assert_eq!(
                    allowed.iter().map(|(m, _)| *m).collect::<Vec<_>>(),
                    vec![Method::Get]
                );
            }
            other => panic!("expected MethodNotAllowed, got {other:?}"),
        }
    }

    #[test]
    fn distinguishes_not_found_from_method_not_allowed() {
        let mut root = Node::default();
        root.insert(&["users"], Method::Get, handler("list_users"))
            .unwrap();

        let mut params = Vec::new();
        assert!(matches!(
            find(&root, "unknown", Method::Get, &mut params),
            MatchOutcome::NotFound
        ));
        assert!(matches!(
            find(&root, "users", Method::Delete, &mut params),
            MatchOutcome::MethodNotAllowed(_)
        ));
    }

    #[test]
    fn overwriting_same_method_replaces_handler() {
        let mut root = Node::default();
        root.insert(&["users"], Method::Get, handler("v1")).unwrap();
        root.insert(&["users"], Method::Get, handler("v2")).unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "users", Method::Get, &mut params);
        assert!(matches!(outcome, MatchOutcome::Matched(h) if h.name == "v2"));
    }

    #[test]
    fn different_methods_same_path_have_independent_handlers() {
        let mut root = Node::default();
        root.insert(&["users"], Method::Get, handler("list_users"))
            .unwrap();
        root.insert(&["users"], Method::Post, handler("create_user"))
            .unwrap();

        let mut params = Vec::new();
        assert!(matches!(
            find(&root, "users", Method::Get, &mut params),
            MatchOutcome::Matched(h) if h.name == "list_users"
        ));
        assert!(matches!(
            find(&root, "users", Method::Post, &mut params),
            MatchOutcome::Matched(h) if h.name == "create_user"
        ));
    }

    #[test]
    fn rejects_unknown_constraint_at_registration() {
        let mut root = Node::default();
        let err = root
            .insert(&["users", "{id:float}"], Method::Get, handler("get_user"))
            .unwrap_err();
        assert_eq!(err, InsertError::UnknownConstraint("float".to_string()));
    }

    #[test]
    fn int_constraint_matches_numeric_segment() {
        let mut root = Node::default();
        root.insert(&["users", "{id:int}"], Method::Get, handler("get_user"))
            .unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "users/42", Method::Get, &mut params);

        assert!(matches!(outcome, MatchOutcome::Matched(h) if h.name == "get_user"));
        assert_eq!(params, vec![("id", "42")]);
    }

    #[test]
    fn int_constraint_rejects_non_numeric_and_falls_through_to_sibling() {
        let mut root = Node::default();
        root.insert(
            &["users", "{id:int}"],
            Method::Get,
            handler("get_user_by_id"),
        )
        .unwrap();
        root.insert(
            &["users", "{slug}"],
            Method::Get,
            handler("get_user_by_slug"),
        )
        .unwrap();

        let mut params = Vec::new();
        let numeric = find(&root, "users/42", Method::Get, &mut params);
        assert!(matches!(numeric, MatchOutcome::Matched(h) if h.name == "get_user_by_id"));
        params.clear();

        let textual = find(&root, "users/abc", Method::Get, &mut params);
        assert!(matches!(textual, MatchOutcome::Matched(h) if h.name == "get_user_by_slug"));
        assert_eq!(params, vec![("slug", "abc")]);
    }

    #[test]
    fn constrained_param_without_matching_sibling_is_not_found() {
        let mut root = Node::default();
        root.insert(&["users", "{id:int}"], Method::Get, handler("get_user"))
            .unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "users/abc", Method::Get, &mut params);
        assert!(matches!(outcome, MatchOutcome::NotFound));
        assert!(params.is_empty());
    }

    #[test]
    fn conflicting_unconstrained_param_names_is_an_error() {
        let mut root = Node::default();
        root.insert(&["users", "{id}"], Method::Get, handler("get_user"))
            .unwrap();
        let err = root
            .insert(&["users", "{slug}"], Method::Get, handler("get_user_alt"))
            .unwrap_err();

        assert_eq!(
            err,
            InsertError::ConflictingParamName {
                existing: "id".to_string(),
                new: "slug".to_string()
            }
        );
    }

    #[test]
    fn registering_same_constrained_param_twice_is_idempotent() {
        let mut root = Node::default();
        root.insert(
            &["users", "{id:int}", "profile"],
            Method::Get,
            handler("profile"),
        )
        .unwrap();
        root.insert(
            &["users", "{id:int}", "posts"],
            Method::Get,
            handler("posts"),
        )
        .unwrap();

        let mut params = Vec::new();
        assert!(matches!(
            find(&root, "users/42/profile", Method::Get, &mut params),
            MatchOutcome::Matched(h) if h.name == "profile"
        ));
        params.clear();
        assert!(matches!(
            find(&root, "users/42/posts", Method::Get, &mut params),
            MatchOutcome::Matched(h) if h.name == "posts"
        ));
    }

    #[test]
    fn matches_wildcard_capturing_remaining_segments() {
        let mut root = Node::default();
        root.insert(
            &["static", "*filepath"],
            Method::Get,
            handler("serve_static"),
        )
        .unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "static/a/b/c.png", Method::Get, &mut params);

        assert!(matches!(outcome, MatchOutcome::Matched(h) if h.name == "serve_static"));
        assert_eq!(params, vec![("filepath", "a/b/c.png")]);
    }

    #[test]
    fn wildcard_is_lowest_priority_after_static_and_param() {
        let mut root = Node::default();
        root.insert(&["files", "*rest"], Method::Get, handler("wildcard"))
            .unwrap();
        root.insert(&["files", "readme"], Method::Get, handler("static"))
            .unwrap();
        root.insert(&["files", "{name}"], Method::Get, handler("param"))
            .unwrap();

        let mut params = Vec::new();
        assert!(matches!(
            find(&root, "files/readme", Method::Get, &mut params),
            MatchOutcome::Matched(h) if h.name == "static"
        ));
        params.clear();
        assert!(matches!(
            find(&root, "files/other", Method::Get, &mut params),
            MatchOutcome::Matched(h) if h.name == "param"
        ));
        params.clear();
        assert!(matches!(
            find(&root, "files/a/b", Method::Get, &mut params),
            MatchOutcome::Matched(h) if h.name == "wildcard"
        ));
    }

    #[test]
    fn wildcard_requires_at_least_one_segment() {
        let mut root = Node::default();
        root.insert(
            &["static", "*filepath"],
            Method::Get,
            handler("serve_static"),
        )
        .unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "static", Method::Get, &mut params);
        assert!(matches!(outcome, MatchOutcome::NotFound));
    }

    #[test]
    fn wildcard_must_be_last_segment_is_a_registration_error() {
        let mut root = Node::default();
        let err = root
            .insert(
                &["static", "*filepath", "extra"],
                Method::Get,
                handler("serve_static"),
            )
            .unwrap_err();
        assert_eq!(err, InsertError::WildcardMustBeLastSegment);
    }

    #[test]
    fn empty_wildcard_name_is_a_registration_error() {
        let mut root = Node::default();
        let err = root
            .insert(&["static", "*"], Method::Get, handler("serve_static"))
            .unwrap_err();
        assert_eq!(err, InsertError::EmptyWildcardName);
    }

    #[test]
    fn conflicting_wildcard_names_is_a_registration_error() {
        let mut root = Node::default();
        root.insert(&["static", "*filepath"], Method::Get, handler("a"))
            .unwrap();
        let err = root
            .insert(&["static", "*path"], Method::Post, handler("b"))
            .unwrap_err();
        assert_eq!(
            err,
            InsertError::ConflictingParamName {
                existing: "filepath".to_string(),
                new: "path".to_string()
            }
        );
    }

    #[test]
    fn stores_and_returns_middleware_for_route() {
        let mut root = Node::default();
        root.insert(
            &["users"],
            Method::Get,
            RouteHandler {
                name: "list_users".to_string(),
                middleware: vec!["auth".to_string(), "log".to_string()],
            },
        )
        .unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "users", Method::Get, &mut params);
        assert!(matches!(outcome, MatchOutcome::Matched(h) if h.middleware == vec!["auth", "log"]));
    }

    #[test]
    fn middleware_is_empty_by_default() {
        let mut root = Node::default();
        root.insert(&["users"], Method::Get, handler("list_users"))
            .unwrap();

        let mut params = Vec::new();
        let outcome = find(&root, "users", Method::Get, &mut params);
        assert!(matches!(outcome, MatchOutcome::Matched(h) if h.middleware.is_empty()));
    }

    #[test]
    fn removes_registered_route() {
        let mut root = Node::default();
        root.insert(&["users"], Method::Get, handler("list_users"))
            .unwrap();

        assert!(root.remove(&["users"], Method::Get).unwrap());

        let mut params = Vec::new();
        assert!(matches!(
            find(&root, "users", Method::Get, &mut params),
            MatchOutcome::NotFound
        ));
    }

    #[test]
    fn removing_unregistered_route_returns_false() {
        let mut root = Node::default();
        root.insert(&["users"], Method::Get, handler("list_users"))
            .unwrap();

        assert!(!root.remove(&["unknown"], Method::Get).unwrap());
        assert!(!root.remove(&["users"], Method::Post).unwrap());
    }

    #[test]
    fn removing_one_method_leaves_others_intact() {
        let mut root = Node::default();
        root.insert(&["users"], Method::Get, handler("list_users"))
            .unwrap();
        root.insert(&["users"], Method::Post, handler("create_user"))
            .unwrap();

        assert!(root.remove(&["users"], Method::Get).unwrap());

        let mut params = Vec::new();
        assert!(matches!(
            find(&root, "users", Method::Get, &mut params),
            MatchOutcome::MethodNotAllowed(_)
        ));
        assert!(matches!(
            find(&root, "users", Method::Post, &mut params),
            MatchOutcome::Matched(h) if h.name == "create_user"
        ));
    }

    #[test]
    fn removing_with_wrong_constraint_does_not_affect_registered_pattern() {
        let mut root = Node::default();
        root.insert(&["users", "{id:int}"], Method::Get, handler("get_user"))
            .unwrap();

        assert!(!root.remove(&["users", "{id}"], Method::Get).unwrap());

        let mut params = Vec::new();
        assert!(matches!(
            find(&root, "users/42", Method::Get, &mut params),
            MatchOutcome::Matched(h) if h.name == "get_user"
        ));
    }

    #[test]
    fn removing_with_unknown_constraint_is_an_error() {
        let mut root = Node::default();
        root.insert(&["users", "{id:int}"], Method::Get, handler("get_user"))
            .unwrap();

        assert_eq!(
            root.remove(&["users", "{id:float}"], Method::Get)
                .unwrap_err(),
            InsertError::UnknownConstraint("float".to_string())
        );
    }

    #[test]
    fn removes_wildcard_route() {
        let mut root = Node::default();
        root.insert(
            &["static", "*filepath"],
            Method::Get,
            handler("serve_static"),
        )
        .unwrap();

        assert!(root.remove(&["static", "*filepath"], Method::Get).unwrap());

        let mut params = Vec::new();
        assert!(matches!(
            find(&root, "static/a/b", Method::Get, &mut params),
            MatchOutcome::NotFound
        ));
    }

    #[test]
    fn lists_all_registered_routes() {
        let mut root = Node::default();
        root.insert(&["health"], Method::Get, handler("health_check"))
            .unwrap();
        root.insert(&["users", "{id:int}"], Method::Get, handler("get_user"))
            .unwrap();
        root.insert(&["users"], Method::Post, handler("create_user"))
            .unwrap();
        root.insert(
            &["static", "*filepath"],
            Method::Get,
            handler("serve_static"),
        )
        .unwrap();

        let mut routes: Vec<(String, Method, String)> = root
            .routes()
            .into_iter()
            .map(|(path, method, handler)| (path, method, handler.name))
            .collect();
        routes.sort();

        assert_eq!(
            routes,
            vec![
                (
                    "/health".to_string(),
                    Method::Get,
                    "health_check".to_string()
                ),
                (
                    "/static/*filepath".to_string(),
                    Method::Get,
                    "serve_static".to_string()
                ),
                (
                    "/users".to_string(),
                    Method::Post,
                    "create_user".to_string()
                ),
                (
                    "/users/{id:int}".to_string(),
                    Method::Get,
                    "get_user".to_string()
                ),
            ]
        );
    }

    #[test]
    fn routes_reflects_removal() {
        let mut root = Node::default();
        root.insert(&["users"], Method::Get, handler("list_users"))
            .unwrap();
        root.remove(&["users"], Method::Get).unwrap();

        assert!(root.routes().is_empty());
    }

    #[test]
    fn routes_includes_middleware() {
        let mut root = Node::default();
        root.insert(
            &["users"],
            Method::Get,
            RouteHandler {
                name: "list_users".to_string(),
                middleware: vec!["auth".to_string()],
            },
        )
        .unwrap();

        let routes = root.routes();
        assert_eq!(routes.len(), 1);
        assert_eq!(routes[0].2.middleware, vec!["auth"]);
    }
}
