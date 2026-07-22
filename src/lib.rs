//! Korvex — a high-performance, Rust-powered router for Python.
//!
//! This crate exposes a single `_core` extension module consumed by the
//! `korvex` Python package; end users should `import korvex`, not this
//! module directly.

use pyo3::exceptions::PyException;
use pyo3::prelude::*;

mod router;

use router::Router;

pyo3::create_exception!(
    _core,
    MethodNotAllowed,
    PyException,
    "Raised by `Router.match_route` when `path` matches a registered route \
     but not for the requested method. `args[0]` is the list of methods \
     that are allowed for that path."
);

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Router>()?;
    m.add("MethodNotAllowed", m.py().get_type::<MethodNotAllowed>())?;
    Ok(())
}
