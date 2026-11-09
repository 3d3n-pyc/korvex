//! The `korvex._core` Python extension: a radix-tree router.
//!
//! Users import `korvex`, which wraps this module. Only this file knows
//! about Python; `tree`, `path` and `method` are plain Rust.

use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyValueError};
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyDict, PyMappingProxy, PyTuple, PyType};

mod escape;
mod method;
mod path;
#[cfg(unix)]
mod proxy;
#[cfg(unix)] // the built-in server relies on Unix sockets; Windows uses an ASGI server
mod server;
mod tree;
#[cfg(unix)]
mod websocket;

use method::Method;
use path::{Constraint, Constraints};
use tree::{Node, Outcome, Route};

create_exception!(
    _core,
    MethodNotAllowed,
    PyException,
    "The path exists, but not for this method (HTTP 405). \
     `allowed` lists the methods that are registered for it."
);

/// `(handler, params, middleware)`, e.g. `("get_user", {"id": 42}, ("auth",))`.
/// Immutable (params is a read-only mapping), so `Router` can cache and share it.
type Match<'py> = (String, Bound<'py, PyMappingProxy>, Bound<'py, PyTuple>);

/// `(path, method, handler, middleware)`, as listed by `routes()`.
type RouteInfo<'py> = (String, &'static str, String, Bound<'py, PyTuple>);

/// The compiled router. `korvex.router.Router` subclasses it to add the
/// `get`/`post`/... shortcuts and `group()`.
#[pyclass(subclass, module = "korvex._core")]
struct Router {
    root: Node<Route>,
    constraints: Constraints,
}

#[pymethods]
impl Router {
    #[new]
    fn new() -> Self {
        Self {
            root: Node::default(),
            constraints: Constraints::default(),
        }
    }

    /// Defines a constraint for patterns, beyond `int`, `uuid` and `str`:
    /// `constraint("slug", "[a-z0-9]+(?:-[a-z0-9]+)*")`, then `{post:slug}`.
    /// A value matches when the whole of it matches the regex (Rust's
    /// `regex` syntax: no look-around or backreferences, linear time).
    /// Define it before the routes that use it; it can't be redefined
    /// differently. Raises `ValueError` for a bad name or regex.
    fn constraint(&mut self, name: &str, pattern: &str) -> PyResult<()> {
        self.constraints
            .define(name, pattern)
            .map_err(PyValueError::new_err)
    }

    /// Every constraint defined with `constraint()`, as `(name, pattern)`.
    fn constraints(&self) -> Vec<(String, String)> {
        self.constraints.list()
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
            .insert_with(path, parse_method(method)?, route, &self.constraints)
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
                for (name, constraint, value) in params {
                    dict.set_item(name, convert(py, constraint, value)?)?;
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
            .remove_with(path, parse_method(method)?, &self.constraints)
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

/// `uuid.UUID`, imported once.
static UUID: PyOnceLock<Py<PyType>> = PyOnceLock::new();

/// Turns a captured value into what its constraint promises: an `int` for
/// `{id:int}`, a `uuid.UUID` for `{id:uuid}`, a `str` otherwise (custom
/// constraints included).
fn convert<'py>(
    py: Python<'py>,
    constraint: Constraint,
    value: &str,
) -> PyResult<Bound<'py, PyAny>> {
    match constraint {
        Constraint::Any | Constraint::Custom(_) => Ok(value.into_pyobject(py)?.into_any()),
        Constraint::Int => {
            let number: i64 = value
                .parse()
                .map_err(|_| PyValueError::new_err(format!("not an integer: {value:?}")))?;
            Ok(number.into_pyobject(py)?.into_any())
        }
        Constraint::Uuid => UUID.import(py, "uuid", "UUID")?.call1((value,)),
    }
}

fn parse_method(method: &str) -> PyResult<Method> {
    Method::parse(method)
        .ok_or_else(|| PyValueError::new_err(format!("unknown HTTP method: {method:?}")))
}

/// Escape `& < > " '` in a str; returns the same object when nothing changes.
#[pyfunction]
fn escape_html<'py>(s: &Bound<'py, pyo3::types::PyString>) -> PyResult<Bound<'py, PyAny>> {
    match escape::escape(&s.to_cow()?) {
        std::borrow::Cow::Borrowed(_) => Ok(s.clone().into_any()),
        std::borrow::Cow::Owned(o) => Ok(pyo3::types::PyString::new(s.py(), &o).into_any()),
    }
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Router>()?;
    m.add_function(wrap_pyfunction!(escape_html, m)?)?;
    #[cfg(unix)]
    {
        m.add_class::<server::Server>()?;
        m.add_class::<server::Responder>()?;
        m.add_class::<websocket::WsConnection>()?;
    }
    m.add("MethodNotAllowed", m.py().get_type::<MethodNotAllowed>())?;
    Ok(())
}
