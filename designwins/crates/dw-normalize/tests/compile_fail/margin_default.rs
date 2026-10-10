//! C9.3 / PRD-NORMALIZE §5.4: margin is never silent.
//!
//! A `Default` for `Margin` would imply "no margin" without the caller
//! choosing it. `margin_pct` must be passed explicitly or explicitly `None`
//! (`Margin::NONE`). A silent 0% is a lie.

use dw_normalize::Margin;

fn main() {
    let margin = Margin::default();
    let _ = margin;
}
