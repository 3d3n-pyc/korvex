//! The pure-Rust routing engine: radix-tree storage and matching.
//!
//! Nothing under this module depends on `PyO3` — it's tested and reasoned
//! about independently of the Python bindings, which live one level up in
//! `super::bindings` and talk to this module only through the items
//! re-exported here.

mod handler;
mod method;
mod node;
mod path;
mod segment;

pub use handler::{MatchOutcome, RouteHandler};
pub use method::Method;
pub use node::Node;
pub use path::PathSegments;
