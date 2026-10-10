//! C10: an `Embedding` cannot exist without a `WeightFingerprint`.
//!
//! If this compiled, a vector could enter the index with no identity, and
//! "a second embedder is unrepresentable" would be unenforceable.

use dw_types::*;

fn main() {
    // Private fields: no fingerprinted vector can be conjured.
    let _embedding = Embedding {
        vector: vec![0.0, 1.0],
        fingerprint: WeightFingerprint::new("bge-large-en-v1.5").unwrap(),
    };
}
