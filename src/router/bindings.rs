//! `PyO3` bindings exposing the [`engine`](super::engine) as a `Router` class.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;

use super::engine::{MatchOutcome, Method, Node, PathSegments, RouteHandler};
use crate::MethodNotAllowed;

/// A route matched successfully: the opaque handler name, any captured
/// path parameters, and the ordered list of opaque middleware names
/// attached to the route, e.g. `("get_user", {"id": "42"}, ["auth"])`.
type MatchResult = (String, HashMap<String, String>, Vec<String>);

/// A high-performance router backed by a Rust radix tree.
///
/// Routes support static segments (`/health`), dynamic segments
/// (`/users/{id}`, optionally constrained: `/users/{id:int}`), and a
/// trailing wildcard (`/static/*filepath`). Static segments always take
/// priority over dynamic ones at the same path depth, dynamic over
/// wildcard. Each route is registered under an HTTP method (default
/// `"GET"`); matching a path that exists but not for the requested method
/// raises [`MethodNotAllowed`] rather than returning `None`, so callers
/// can distinguish 404 from 405.
///
/// [`Router::add_route`] covers the general case; [`Router::get`],
/// [`Router::post`], etc. are shorthand for the common one. For grouping
/// routes under a shared path prefix and/or middleware, see
/// `korvex.RouteGroup` (`router.group(prefix, middleware=...)`), added by
/// `korvex.Router` — a pure-Python subclass of this class, in
/// `korvex/router.py` — since it's registration-only sugar with no effect
/// on `match_route`'s cost. `subclass` so that pure-Python subclass (and,
/// if a caller wants, their own) is possible at all: `#[pyclass]` types
/// aren't subclassable in Python by default.
#[pyclass(subclass)]
pub struct Router {
    root: Node,
}

#[pymethods]
impl Router {
    /// Creates an empty router.
    #[new]
    fn new() -> Self {
        Router {
            root: Node::default(),
        }
    }

    /// Registers a route.
    ///
    /// `path` uses `{name}` to mark a dynamic segment, e.g.
    /// `"/users/{id}/posts/{post_id}"`, optionally restricted by a
    /// constraint, e.g. `"/users/{id:int}"` (`int`, `uuid`, or `str` for
    /// no restriction — the default), and `*name` as its last segment to
    /// mark a wildcard capturing the rest of the path, e.g.
    /// `"/static/*filepath"`. `handler_name` is an opaque string
    /// returned by [`Router::match_route`] on a match — Korvex does not
    /// interpret it, callers are free to use it as a lookup key into
    /// their own handler registry. `method` is an HTTP method name
    /// (`"GET"`, `"POST"`, ...); registering the same path and method
    /// again replaces the previous handler. `middleware` is an optional
    /// ordered list of opaque names, also uninterpreted by Korvex,
    /// returned alongside the handler on a match. Raises `ValueError` if
    /// `method` or a constraint name is unrecognized, or if this path
    /// registers a dynamic segment under a name that conflicts with one
    /// already registered at the same depth and constraint.
    #[pyo3(signature = (path, handler_name, method="GET", middleware=None))]
    fn add_route(
        &mut self,
        path: &str,
        handler_name: String,
        method: &str,
        middleware: Option<Vec<String>>,
    ) -> PyResult<()> {
        let method = parse_method(method)?;
        self.add_route_for(path, handler_name, method, middleware)
    }

    /// Shorthand for `add_route(path, handler_name, method="GET", middleware=middleware)`.
    #[pyo3(signature = (path, handler_name, middleware=None))]
    fn get(
        &mut self,
        path: &str,
        handler_name: String,
        middleware: Option<Vec<String>>,
    ) -> PyResult<()> {
        self.add_route_for(path, handler_name, Method::Get, middleware)
    }

    /// Shorthand for `add_route(path, handler_name, method="POST", middleware=middleware)`.
    #[pyo3(signature = (path, handler_name, middleware=None))]
    fn post(
        &mut self,
        path: &str,
        handler_name: String,
        middleware: Option<Vec<String>>,
    ) -> PyResult<()> {
        self.add_route_for(path, handler_name, Method::Post, middleware)
    }

    /// Shorthand for `add_route(path, handler_name, method="PUT", middleware=middleware)`.
    #[pyo3(signature = (path, handler_name, middleware=None))]
    fn put(
        &mut self,
        path: &str,
        handler_name: String,
        middleware: Option<Vec<String>>,
    ) -> PyResult<()> {
        self.add_route_for(path, handler_name, Method::Put, middleware)
    }

    /// Shorthand for `add_route(path, handler_name, method="PATCH", middleware=middleware)`.
    #[pyo3(signature = (path, handler_name, middleware=None))]
    fn patch(
        &mut self,
        path: &str,
        handler_name: String,
        middleware: Option<Vec<String>>,
    ) -> PyResult<()> {
        self.add_route_for(path, handler_name, Method::Patch, middleware)
    }

    /// Shorthand for `add_route(path, handler_name, method="DELETE", middleware=middleware)`.
    #[pyo3(signature = (path, handler_name, middleware=None))]
    fn delete(
        &mut self,
        path: &str,
        handler_name: String,
        middleware: Option<Vec<String>>,
    ) -> PyResult<()> {
        self.add_route_for(path, handler_name, Method::Delete, middleware)
    }

    /// Shorthand for `add_route(path, handler_name, method="HEAD", middleware=middleware)`.
    #[pyo3(signature = (path, handler_name, middleware=None))]
    fn head(
        &mut self,
        path: &str,
        handler_name: String,
        middleware: Option<Vec<String>>,
    ) -> PyResult<()> {
        self.add_route_for(path, handler_name, Method::Head, middleware)
    }

    /// Shorthand for `add_route(path, handler_name, method="OPTIONS", middleware=middleware)`.
    #[pyo3(signature = (path, handler_name, middleware=None))]
    fn options(
        &mut self,
        path: &str,
        handler_name: String,
        middleware: Option<Vec<String>>,
    ) -> PyResult<()> {
        self.add_route_for(path, handler_name, Method::Options, middleware)
    }

    /// Matches `path` and `method` against registered routes.
    ///
    /// Returns `(handler_name, params, middleware)` on success, `None` if
    /// no route matches `path` at all (404). Raises [`MethodNotAllowed`] if
    /// a route matches `path` but not for `method` (405) — its single
    /// argument is the list of methods that *are* allowed. `params` maps
    /// each dynamic segment's name to the matched value from `path`.
    #[pyo3(signature = (path, method="GET"))]
    fn match_route(&self, path: &str, method: &str) -> PyResult<Option<MatchResult>> {
        let method = parse_method(method)?;
        let mut params = Vec::new();
        match self.root.find(PathSegments::new(path), method, &mut params) {
            MatchOutcome::Matched(handler) => {
                let params = params
                    .into_iter()
                    .map(|(name, value)| (name.to_string(), value.to_string()))
                    .collect();
                Ok(Some((
                    handler.name.clone(),
                    params,
                    handler.middleware.clone(),
                )))
            }
            MatchOutcome::NotFound => Ok(None),
            MatchOutcome::MethodNotAllowed(allowed) => Err(method_not_allowed(allowed)),
        }
    }

    /// Removes the route registered at exactly `path` (the same syntax
    /// `add_route` takes, e.g. `"/users/{id:int}"` — not an example URL)
    /// for `method`.
    ///
    /// Returns `True` if a route was removed, `False` if no route was
    /// registered at that exact path pattern and method — removing
    /// something never registered is not an error. Raises `ValueError`
    /// for the same reason `add_route` would (an unrecognized method or
    /// constraint name).
    #[pyo3(signature = (path, method="GET"))]
    fn remove_route(&mut self, path: &str, method: &str) -> PyResult<bool> {
        let method = parse_method(method)?;
        let segments = split_path(path);
        self.root
            .remove(&segments, method)
            .map_err(|e| PyValueError::new_err(e.to_string()))
    }

    /// Every registered route, as a list of `(path, method, handler_name,
    /// middleware)` tuples — for debugging, generating docs/OpenAPI, or
    /// building an admin view. Not performance-sensitive, and never
    /// called from `match_route`'s hot path. Sorted by `(path, method)`
    /// for a stable, predictable order across calls.
    fn routes(&self) -> Vec<(String, &'static str, String, Vec<String>)> {
        let mut routes: Vec<_> = self
            .root
            .routes()
            .into_iter()
            .map(|(path, method, handler)| {
                (path, method.as_str(), handler.name, handler.middleware)
            })
            .collect();
        routes.sort_by(|a, b| (&a.0, a.1).cmp(&(&b.0, b.1)));
        routes
    }

    fn __repr__(&self) -> String {
        format!("Router(routes={})", self.root.routes().len())
    }
}

impl Router {
    /// Shared implementation behind [`Router::add_route`] and the
    /// per-method shorthands (`get`, `post`, ...) — not itself exposed to
    /// Python, so the seven shorthands stay one-line dispatchers onto a
    /// single real implementation rather than duplicating it.
    fn add_route_for(
        &mut self,
        path: &str,
        handler_name: String,
        method: Method,
        middleware: Option<Vec<String>>,
    ) -> PyResult<()> {
        let segments = split_path(path);
        let handler = RouteHandler {
            name: handler_name,
            middleware: middleware.unwrap_or_default(),
        };
        self.root
            .insert(&segments, method, handler)
            .map_err(|e| PyValueError::new_err(e.to_string()))
    }
}

fn parse_method(method: &str) -> PyResult<Method> {
    Method::parse(method)
        .ok_or_else(|| PyValueError::new_err(format!("unknown HTTP method: {method:?}")))
}

fn method_not_allowed(allowed: &[(Method, RouteHandler)]) -> PyErr {
    let allowed: Vec<&str> = allowed.iter().map(|(m, _)| m.as_str()).collect();
    MethodNotAllowed::new_err(allowed)
}

/// Splits a path into its non-empty segments, e.g. `"/users/42/"` ->
/// `["users", "42"]`. Used by [`Router::add_route`], which runs once per
/// registered route rather than per lookup, so collecting [`PathSegments`]
/// into a `Vec` here (for [`Node::insert`]'s slice-based recursion) isn't
/// perf-sensitive the way [`Router::match_route`]'s lookup path is.
fn split_path(path: &str) -> Vec<&str> {
    PathSegments::new(path).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn split_path_ignores_empty_segments() {
        assert_eq!(split_path("/users/42/"), vec!["users", "42"]);
        assert_eq!(split_path("users/42"), vec!["users", "42"]);
        assert_eq!(split_path("/"), Vec::<&str>::new());
    }
}
