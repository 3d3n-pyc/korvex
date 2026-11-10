//! Fuzzes route matching and path splitting: they must never panic, and a
//! captured parameter must be a piece of the path.
#![no_main]
#![allow(dead_code)]

#[path = "../../src/method.rs"]
mod method;
#[path = "../../src/path.rs"]
mod path;
#[path = "../../src/tree.rs"]
mod tree;

use libfuzzer_sys::fuzz_target;
use method::Method;
use path::{Constraints, Segment, Segments};
use std::sync::OnceLock;
use tree::{Node, Outcome};

const METHODS: [&str; 8] = [
    "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "GET",
];

fn router() -> &'static Node<u32> {
    static ROUTER: OnceLock<Node<u32>> = OnceLock::new();
    ROUTER.get_or_init(|| {
        let mut constraints = Constraints::default();
        constraints
            .define("slug", "[a-z0-9]+(-[a-z0-9]+)*")
            .unwrap();
        let routes = [
            ("/", "GET"),
            ("/users/{id:int}", "GET"),
            ("/users/{id:int}", "DELETE"),
            ("/users/{id:int}/posts/{post:slug}", "GET"),
            ("/items/{id:uuid}", "GET"),
            ("/items/{id:uuid}", "PUT"),
            ("/static/*path", "GET"),
            ("/static/*path", "HEAD"),
            ("/a/{x}/b/{y:int}", "POST"),
            ("/a/literal/b", "PATCH"),
        ];
        let mut node = Node::default();
        for (n, (pattern, method)) in routes.iter().enumerate() {
            let method = Method::parse(method).unwrap();
            node.insert_with(pattern, method, n as u32, &constraints)
                .unwrap();
        }
        node
    })
}

fuzz_target!(|data: &[u8]| {
    let Some((first, rest)) = data.split_first() else {
        return;
    };
    let path = String::from_utf8_lossy(rest);
    let method = Method::parse(METHODS[usize::from(*first) % METHODS.len()]).unwrap();
    let mut params = Vec::new();
    if let Outcome::Matched(_) = router().find(&path, method, &mut params) {
        for (_, _, value) in &params {
            assert!(path.contains(value), "{value:?} is not in {path:?}");
        }
    }
    let _ = Segments::new(&path).count();
    // Arbitrary text as a route pattern: an error is fine, a panic is not.
    let constraints = Constraints::default();
    let mut tree: Node<u32> = Node::default();
    let _ = tree.insert_with(&path, method, 0, &constraints);
    for segment in Segments::new(&path) {
        let _ = Segment::parse_with(segment, &constraints);
    }
    let _ = tree.remove_with(&path, method, &constraints);
});
