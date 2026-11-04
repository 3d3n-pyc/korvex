//! The `korvex._core` Python extension: a radix-tree router.
//!
//! Users import `korvex`, which wraps this module. The routing logic in
//! `tree`, `path` and `method` is plain Rust; only this file knows about
//! Python.

use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyMappingProxy, PyTuple};

mod method;
mod path;
mod tree;

use method::Method;
use tree::{Node, Outcome, Route};

create_exception!(
    _core,
    MethodNotAllowed,
    PyException,
    "The path exists, but not for this method (HTTP 405). \
     `allowed` lists the methods that are registered for it."
);

/// `(handler, params, middleware)`, e.g. `("get_user", {"id": "42"}, ("auth",))`.
/// Fully immutable (params is a read-only mapping), so `korvex.Router` can
/// cache and share it between calls.
type Match<'py> = (String, Bound<'py, PyMappingProxy>, Bound<'py, PyTuple>);

/// `(path, method, handler, middleware)`, as listed by `routes()`.
type RouteInfo<'py> = (String, &'static str, String, Bound<'py, PyTuple>);

/// The compiled router. `korvex.Router` subclasses it to add the
/// `get`/`post`/... shortcuts and `group()`.
#[pyclass(subclass, module = "korvex._core")]
struct Router {
    root: Node,
}

#[pymethods]
impl Router {
    #[new]
    fn new() -> Self {
        Self {
            root: Node::default(),
        }
    }

    /// Registers `handler` for `path` and `method`, replacing any route
    /// already there. Raises `ValueError` for an invalid path or method.
    #[pyo3(signature = (path, handler, *, method = "GET", middleware = None))]
    fn route(
        &mut self,
        path: &str,
        handler: String,
        method: &str,
        middleware: Option<Vec<String>>,
    ) -> PyResult<()> {
        let route = Route {
            handler,
            middleware: middleware.unwrap_or_default(),
        };
        self.root
            .insert(path, parse_method(method)?, route)
            .map_err(|e| PyValueError::new_err(e.to_string()))
    }

    /// Returns `(handler, params, middleware)`, or `None` if nothing
    /// matches `path`. Raises `MethodNotAllowed` if `path` matches but
    /// not for `method`.
    #[pyo3(name = "match", signature = (path, method = "GET"))]
    fn find<'py>(&self, py: Python<'py>, path: &str, method: &str) -> PyResult<Option<Match<'py>>> {
        let mut params = Vec::new();
        match self.root.find(path, parse_method(method)?, &mut params) {
            Outcome::Matched(route) => {
                let dict = PyDict::new(py);
                for (name, value) in params {
                    dict.set_item(name, value)?;
                }
                Ok(Some((
                    route.handler.clone(),
                    PyMappingProxy::new(py, dict.as_mapping()),
                    PyTuple::new(py, &route.middleware)?,
                )))
            }
            Outcome::NotFound => Ok(None),
            Outcome::MethodNotAllowed(routes) => {
                let allowed: Vec<&str> = routes.iter().map(|(m, _)| m.as_str()).collect();
                let err = MethodNotAllowed::new_err(format!("allowed: {}", allowed.join(", ")));
                err.value(py).setattr("allowed", allowed)?;
                Err(err)
            }
        }
    }

    /// Removes the route registered for exactly `path` (a pattern like
    /// `/users/{id:int}`, not a concrete URL) and `method`. Returns
    /// whether there was one.
    #[pyo3(signature = (path, method = "GET"))]
    fn remove(&mut self, path: &str, method: &str) -> PyResult<bool> {
        self.root
            .remove(path, parse_method(method)?)
            .map_err(|e| PyValueError::new_err(e.to_string()))
    }

    /// Every route as `(path, method, handler, middleware)`, sorted by path
    /// then method.
    fn routes<'py>(&self, py: Python<'py>) -> PyResult<Vec<RouteInfo<'py>>> {
        let mut routes = self.root.routes();
        routes.sort_by(|a, b| (&a.0, a.1).cmp(&(&b.0, b.1)));
        routes
            .into_iter()
            .map(|(path, method, route)| {
                let middleware = PyTuple::new(py, route.middleware)?;
                Ok((path, method.as_str(), route.handler, middleware))
            })
            .collect()
    }

    fn __repr__(&self) -> String {
        format!("Router(routes={})", self.root.routes().len())
    }
}

fn parse_method(method: &str) -> PyResult<Method> {
    Method::parse(method)
        .ok_or_else(|| PyValueError::new_err(format!("unknown HTTP method: {method:?}")))
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Router>()?;
    m.add("MethodNotAllowed", m.py().get_type::<MethodNotAllowed>())?;
    Ok(())
}
