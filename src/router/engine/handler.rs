//! What a route resolves to, and how a [`super::node::Node`] looks that up
//! by HTTP method.

use super::method::Method;

/// What a route resolves to: an opaque handler name plus an ordered list
/// of opaque middleware names. Korvex interprets neither — callers use
/// them as lookup keys into their own handler/middleware registries.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RouteHandler {
    pub name: String,
    pub middleware: Vec<String>,
}

/// The per-node registry of `Method -> RouteHandler`.
///
/// A `Vec` rather than a nested hash map: a node registers at most a
/// handful of methods (in practice 1-2), so a linear scan avoids the
/// allocation and hashing overhead a `HashMap` would add for no benefit
/// at this size. `pub(super)`: only [`super::node::Node`] needs this —
/// everything outside the engine deals in [`RouteHandler`]/[`MatchOutcome`].
#[derive(Default, Debug)]
pub(super) struct MethodTable(Vec<(Method, RouteHandler)>);

impl MethodTable {
    pub(super) fn insert(&mut self, method: Method, handler: RouteHandler) {
        if let Some(slot) = self.0.iter_mut().find(|(m, _)| *m == method) {
            slot.1 = handler;
        } else {
            self.0.push((method, handler));
        }
    }

    /// Resolves this table against a requested method: [`MatchOutcome::NotFound`]
    /// if the table is empty (this node isn't a registered route at all),
    /// [`MatchOutcome::MethodNotAllowed`] if it's a route but not for this
    /// method, [`MatchOutcome::Matched`] otherwise.
    pub(super) fn outcome(&self, method: Method) -> MatchOutcome<'_> {
        if self.0.is_empty() {
            return MatchOutcome::NotFound;
        }
        match self.0.iter().find(|(m, _)| *m == method) {
            Some((_, handler)) => MatchOutcome::Matched(handler),
            None => MatchOutcome::MethodNotAllowed(&self.0),
        }
    }

    /// Removes the entry for `method`, if any. Returns whether one was
    /// actually removed.
    pub(super) fn remove(&mut self, method: Method) -> bool {
        let len_before = self.0.len();
        self.0.retain(|(m, _)| *m != method);
        self.0.len() != len_before
    }

    /// Every `(method, handler)` entry, for introspection
    /// ([`super::node::Node::routes`]) — not used on the matching path.
    pub(super) fn entries(&self) -> impl Iterator<Item = (Method, &RouteHandler)> {
        self.0.iter().map(|(m, h)| (*m, h))
    }
}

/// The result of matching a path against the tree for a given method.
#[derive(Debug)]
pub enum MatchOutcome<'a> {
    /// No route registered for this path, for any method.
    NotFound,
    /// A route is registered at this path, but not for the requested
    /// method. Carries the methods that *are* allowed, so callers can
    /// build a `405` response (e.g. an `Allow` header).
    MethodNotAllowed(&'a [(Method, RouteHandler)]),
    Matched(&'a RouteHandler),
}
