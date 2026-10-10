//! PRD-NORMALIZE §6.3: a typed condition cannot be conjured from JSON.
//!
//! `Deserialize` is deliberately absent so the fail-closed laws live in
//! `parse_condition` and cannot be routed around.

use dw_normalize::TypedCondition;

fn main() {
    let typed: TypedCondition = serde_json::from_str("{}").unwrap();
    let _ = typed;
}
