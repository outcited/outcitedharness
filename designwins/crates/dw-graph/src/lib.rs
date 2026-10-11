//! # dw-graph — the part number as a second printed source
//!
//! Vendor ordering-code decoders (`harness/electronics/ordering_codes.py`,
//! ported 1:1): every MCU vendor prints a part-numbering figure, so decoding
//! a part number states flash size, pin count and package **a second time**,
//! through a different process than the device table. That makes it an
//! independent source for the two-source rule (Tier B).
//!
//! Only schemes that decode unambiguously from the part number alone are
//! here. A family whose letters mean different things across lines is
//! deliberately left out: returning nothing is the honest answer.
//!
//! [`topology`] is the other half: the printed topology + integration class
//! reader (`power_topology.py`), fail-closed — an answer that is not printed
//! is absent, never guessed.
//!
//! This crate calls no models and is `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod ordering;
pub mod topology;

pub use ordering::{decode, source_label, OrderingFacts};
pub use topology::{read_topology, Evidence, PageText, TopologyRead};
