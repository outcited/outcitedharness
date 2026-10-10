//! Condition coverage — `PRD-NORMALIZE` §5.2.
//!
//! Port of `harness/electronics/condition_model.py::condition_covers`.
//!
//! This is the elimination contract (`C9.6`: *a condition must COVER the
//! point*). `Partial`, `Mismatch`, and `Absent` all mean **we could not
//! establish coverage** — which under `C9.1` is `Unknown`, which never
//! eliminates.

use std::collections::BTreeMap;

use crate::condition::CondValue;

/// Relative epsilon used when comparing printed values.
///
/// The reference compares with `1e-9 · max(1, |bounds|)`, so the tolerance is
/// relative to magnitude and never absolute-only. Stored explicitly because
/// `PLAN` §10 forbids silent float tolerance.
pub const VALUE_EPS: f64 = 1e-9;

fn span(v: CondValue) -> (f64, f64) {
    v.span()
}

fn eps(bounds: &[f64]) -> f64 {
    let mut m: f64 = 1.0;
    for b in bounds {
        m = m.max(b.abs());
    }
    VALUE_EPS * m
}

/// Value agreement is **range overlap**, not equality.
///
/// `0 to 10 V` and `5 to 15 V` agree; `0 to 4 V` and `5 to 10 V` do not.
#[must_use]
pub fn values_agree(claim: CondValue, req: CondValue) -> bool {
    let (cb, ce) = span(claim);
    let (rb, re) = span(req);
    let e = eps(&[cb, ce, rb, re]);
    cb <= re + e && rb <= ce + e
}

/// Containment plus at least one strict inequality.
///
/// This is what lifts a match from [`Coverage::Exact`] to [`Coverage::Covers`].
#[must_use]
pub fn claim_strictly_wider(claim: CondValue, req: CondValue) -> bool {
    let (cb, ce) = span(claim);
    let (rb, re) = span(req);
    let e = eps(&[cb, ce, rb, re]);
    let wider = cb <= rb + e && ce >= re - e;
    let strictly = cb < rb - e || ce > re + e;
    wider && strictly
}

/// How a claim's typed condition stands against a requirement's.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Coverage {
    /// Same key sets, values agree on every shared key, no claim value strictly wider.
    Exact,
    /// Agrees on every required key AND is strictly broader.
    Covers,
    /// Shared keys agree, but some required keys are missing.
    Partial,
    /// A shared key disagrees.
    Mismatch,
    /// Claim untyped, or no shared keys.
    Absent,
}

impl Coverage {
    /// The stable string the reference emits.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Exact => "exact",
            Self::Covers => "covers",
            Self::Partial => "partial",
            Self::Mismatch => "mismatch",
            Self::Absent => "absent",
        }
    }

    /// Whether this coverage may satisfy an atom (`C9.6`).
    ///
    /// `Partial`, `Mismatch`, and `Absent` are all "could not establish
    /// coverage", which is `Unknown` — and `Unknown` never eliminates.
    #[must_use]
    pub const fn may_satisfy(self) -> bool {
        matches!(self, Self::Exact | Self::Covers)
    }
}

/// Compare a claim's typed condition against a requirement's.
///
/// **Two deliberate asymmetries at the empty boundary**, per `PRD-NORMALIZE`
/// §5.2. They are properties of the elimination law and must not be "fixed":
///
/// * `condition_covers(claim, {}) == Exact` — a requirement that pins no
///   condition is satisfied by any typed evidence.
/// * `condition_covers({}, req) == Absent` — untyped evidence establishes
///   nothing, so it never covers (`C9.1`).
#[must_use]
pub fn condition_covers(
    claim: Option<&BTreeMap<String, CondValue>>,
    req: Option<&BTreeMap<String, CondValue>>,
) -> Coverage {
    let empty = BTreeMap::new();
    let claim = claim.unwrap_or(&empty);
    let req = req.unwrap_or(&empty);

    if claim.is_empty() {
        return Coverage::Absent;
    }
    if req.is_empty() {
        return Coverage::Exact;
    }

    let shared: Vec<&String> = claim.keys().filter(|k| req.contains_key(*k)).collect();
    if shared.is_empty() {
        return Coverage::Absent;
    }
    for k in &shared {
        if !values_agree(claim[*k], req[*k]) {
            return Coverage::Mismatch;
        }
    }
    if shared.len() != req.len() {
        return Coverage::Partial;
    }
    let same_set = claim.len() == req.len();
    if same_set
        && !shared
            .iter()
            .any(|k| claim_strictly_wider(claim[*k], req[*k]))
    {
        return Coverage::Exact;
    }
    Coverage::Covers
}

#[cfg(test)]
// Tests assert freely: they index, unwrap, and match on single variants to
// prove the laws. Those restrictions are kept in library code and are noise
// here.
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::wildcard_enum_match_arm,
    clippy::match_wildcard_for_single_variants,
    clippy::match_same_arms,
    clippy::too_many_lines
)]
mod tests {
    use super::*;

    fn map(pairs: &[(&str, CondValue)]) -> BTreeMap<String, CondValue> {
        pairs.iter().map(|(k, v)| (k.to_string(), *v)).collect()
    }

    #[test]
    fn empty_boundary_asymmetry_is_deliberate() {
        let x = map(&[("vgs_v", CondValue::Scalar(0.0))]);
        assert_eq!(
            condition_covers(Some(&x), Some(&BTreeMap::new())),
            Coverage::Exact
        );
        assert_eq!(
            condition_covers(Some(&BTreeMap::new()), Some(&x)),
            Coverage::Absent
        );
        assert_eq!(condition_covers(None, Some(&x)), Coverage::Absent);
    }

    /// PRD Gate N2: reflexivity on non-empty typed conditions.
    #[test]
    fn property_coverage_reflexivity() {
        for pairs in [
            &[("vgs_v", CondValue::Scalar(0.0))][..],
            &[
                ("vgs_v", CondValue::Scalar(0.0)),
                ("ta_c", CondValue::Scalar(25.0)),
            ][..],
            &[("f_hz", CondValue::Range(1.0, 1e6))][..],
        ] {
            let x = map(pairs);
            assert_eq!(
                condition_covers(Some(&x), Some(&x)),
                Coverage::Exact,
                "{pairs:?}"
            );
        }
    }

    #[test]
    fn values_agree_is_overlap_not_equality() {
        let a = CondValue::Range(0.0, 10.0);
        let b = CondValue::Range(5.0, 15.0);
        assert!(values_agree(a, b), "overlapping ranges agree");
        assert!(values_agree(a, a), "identical agree");
        assert!(!values_agree(
            CondValue::Range(0.0, 4.0),
            CondValue::Range(5.0, 10.0)
        ));
    }

    #[test]
    fn strictly_wider_lifts_exact_to_covers() {
        let claim = map(&[("ta_c", CondValue::Range(-40.0, 150.0))]);
        let req = map(&[("ta_c", CondValue::Scalar(25.0))]);
        assert_eq!(condition_covers(Some(&claim), Some(&req)), Coverage::Covers);
    }

    #[test]
    fn mismatch_and_partial_and_absent() {
        let a = map(&[("vgs_v", CondValue::Scalar(0.0))]);
        let b = map(&[("vgs_v", CondValue::Scalar(10.0))]);
        assert_eq!(condition_covers(Some(&a), Some(&b)), Coverage::Mismatch);

        let wider = map(&[
            ("vgs_v", CondValue::Scalar(0.0)),
            ("ta_c", CondValue::Scalar(25.0)),
        ]);
        assert_eq!(condition_covers(Some(&a), Some(&wider)), Coverage::Partial);

        let disjoint = map(&[("f_hz", CondValue::Scalar(1e6))]);
        assert_eq!(
            condition_covers(Some(&a), Some(&disjoint)),
            Coverage::Absent
        );
    }

    #[test]
    fn only_exact_and_covers_may_satisfy() {
        assert!(Coverage::Exact.may_satisfy());
        assert!(Coverage::Covers.may_satisfy());
        assert!(!Coverage::Partial.may_satisfy());
        assert!(!Coverage::Mismatch.may_satisfy());
        assert!(!Coverage::Absent.may_satisfy());
    }
}
