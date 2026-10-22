//! Scenario validation: proposals in, declared atoms + parked reasons out.
//!
//! A scenario is a question plus *proposed* atoms. Proposals are compiled to
//! engine [`dw_decide::Atom`]s only when the knife declaration honors them
//! ([`crate::knife`]); everything else is parked with a machine-readable code.
//! Accepted atom sets are canonicalized (sorted, value-normalized) and keyed
//! by SHA-256 for dedupe and receipt identity.

use std::collections::BTreeMap;

use dw_decide::{Atom, Axis, Op};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::knife::spec_for;
use crate::sha256_hex;

/// One proposed atom, exactly as the generator emitted it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AtomProposal {
    /// Axis name as serde-snake_case (`flash_kb`).
    pub axis: String,
    /// Op name (`min_rating`).
    pub op: String,
    /// The demanded magnitude.
    pub value: Value,
    /// Unit as stated (`kb`, `mhz`, `count`).
    pub unit: Option<String>,
    /// Hard atoms eliminate; the pilot scores hard atoms only.
    #[serde(default = "default_true")]
    pub hard: bool,
}

fn default_true() -> bool {
    true
}

/// Who generated this scenario (provenance of the *question*).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GeneratorRef {
    /// `tp4` or `hand`.
    pub source: String,
    /// Model id, when model-generated.
    pub model: Option<String>,
    /// Endpoint host:port, when model-generated.
    pub endpoint: Option<String>,
    /// SHA-256 of the prompt that produced it, when recorded.
    pub prompt_sha256: Option<String>,
}

/// A scenario row in the job's scenario feed.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Scenario {
    /// Stable id (generator-unique).
    pub scenario_id: String,
    /// The engineering question, verbatim.
    pub question: String,
    /// Occasion wording, when the generator carried one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub occasion: Option<String>,
    /// Bench-able end products named, if any.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub applications: Vec<String>,
    /// The proposed atoms.
    pub proposed_atoms: Vec<AtomProposal>,
    /// Provenance of the question.
    pub generated_by: GeneratorRef,
}

/// Why one proposal was parked. Machine-readable.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "code")]
pub enum ParkCode {
    /// Axis is not in `dw-decide` at all (unsupported).
    UnsupportedAxis,
    /// Axis exists but is not in the pilot's declared knife table
    /// (no honored binding for this feed).
    UndeclaredKnife,
    /// Op does not match the declaration (wrong knife binding).
    WrongBindingOp,
    /// Value is not a finite number.
    NonNumericValue,
    /// Value outside the declared plausible band.
    ValueOutOfRange,
    /// Unit does not match the declaration.
    UnitMismatch,
    /// Soft atom — the pilot scores hard atoms only.
    SoftAtom,
}

impl ParkCode {
    /// Stable `snake_case` name (the wire name, kept in one place).
    #[must_use]
    pub fn code_name(&self) -> &'static str {
        match self {
            Self::UnsupportedAxis => "unsupported_axis",
            Self::UndeclaredKnife => "undeclared_knife",
            Self::WrongBindingOp => "wrong_binding_op",
            Self::NonNumericValue => "non_numeric_value",
            Self::ValueOutOfRange => "value_out_of_range",
            Self::UnitMismatch => "unit_mismatch",
            Self::SoftAtom => "soft_atom",
        }
    }
}

/// What happened to one proposal.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AtomDisposition {
    /// The proposal, verbatim.
    pub proposal: AtomProposal,
    /// Honored → the atom that was compiled; parked → why.
    pub outcome: Result<CanonicalAtom, ParkCode>,
}

/// A canonical, engine-ready atom (serde shape of the canonical set).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CanonicalAtom {
    /// Engine axis name.
    pub axis: String,
    /// Engine op name.
    pub op: String,
    /// Value, normalized (`v * 1e-4` rounded to kill float noise).
    pub value: f64,
    /// Canonical unit.
    pub unit: String,
}

/// A validated scenario.
#[derive(Debug, Clone, PartialEq)]
pub struct Validated {
    /// The original scenario.
    pub scenario: Scenario,
    /// Disposition of every proposal.
    pub dispositions: Vec<AtomDisposition>,
    /// The compiled hard atoms (engine types).
    pub atoms: Vec<Atom>,
    /// Canonical set (sorted by axis) — empty if nothing honored.
    pub canonical: Vec<CanonicalAtom>,
    /// SHA-256 of the canonical set JSON — the atom-set identity.
    pub atom_set_sha256: String,
}

/// Value normalization for dedupe: 4-decimal rounding kills `256` vs `256.0`
/// float noise without hiding real differences.
fn norm_value(v: f64) -> f64 {
    (v * 10_000.0).round() / 10_000.0
}

fn finite_number(v: &Value) -> Option<f64> {
    let n = v.as_f64()?;
    if n.is_finite() {
        Some(n)
    } else {
        None
    }
}

/// Validate one scenario against the knife declaration.
#[must_use]
pub fn validate(scenario: Scenario) -> Validated {
    let mut dispositions = Vec::new();
    let mut canonical = Vec::new();
    let mut atoms = Vec::new();

    let proposals = scenario.proposed_atoms.clone();
    for proposal in proposals {
        let outcome = compile(&proposal);
        match outcome {
            Ok((atom, canon)) => {
                atoms.push(atom);
                canonical.push(canon.clone());
                dispositions.push(AtomDisposition {
                    proposal,
                    outcome: Ok(canon),
                });
            }
            Err(code) => dispositions.push(AtomDisposition {
                proposal,
                outcome: Err(code),
            }),
        }
    }

    canonical.sort_by(|a, b| a.axis.cmp(&b.axis).then_with(|| a.op.cmp(&b.op)));

    let atom_set_sha256 = sha256_hex(canonical_json(&canonical).as_bytes());

    Validated {
        scenario,
        dispositions,
        atoms,
        canonical,
        atom_set_sha256,
    }
}

/// The canonical JSON of an atom set (sorted; stable separators).
#[must_use]
pub fn canonical_json(canonical: &[CanonicalAtom]) -> String {
    serde_json::to_string(canonical).unwrap_or_else(|_| "[]".into())
}

fn compile(proposal: &AtomProposal) -> Result<(Atom, CanonicalAtom), ParkCode> {
    if !proposal.hard {
        return Err(ParkCode::SoftAtom);
    }
    let Some(spec) = spec_for(&proposal.axis) else {
        // Distinguish "engine does not know it" from "engine knows it, the
        // pilot does not declare it".
        let engine_knows =
            serde_json::from_value::<Axis>(serde_json::Value::String(proposal.axis.clone()))
                .is_ok();
        return Err(if engine_knows {
            ParkCode::UndeclaredKnife
        } else {
            ParkCode::UnsupportedAxis
        });
    };
    if proposal.op != spec.op {
        return Err(ParkCode::WrongBindingOp);
    }
    let value = finite_number(&proposal.value).ok_or(ParkCode::NonNumericValue)?;
    if !(spec.value_min..=spec.value_max).contains(&value) {
        return Err(ParkCode::ValueOutOfRange);
    }
    if let Some(unit) = &proposal.unit {
        if unit.to_lowercase() != spec.unit {
            return Err(ParkCode::UnitMismatch);
        }
    }
    let axis = spec.engine_axis().ok_or(ParkCode::UnsupportedAxis)?;
    let op: Op = spec.engine_op().ok_or(ParkCode::WrongBindingOp)?;
    let value = norm_value(value);
    let atom = Atom::scalar(axis, op, value, true);
    let canon = CanonicalAtom {
        axis: spec.axis.to_string(),
        op: spec.op.to_string(),
        value,
        unit: spec.unit.to_string(),
    };
    Ok((atom, canon))
}

impl Validated {
    /// A scenario is scorable when at least one hard atom was honored.
    #[must_use]
    pub fn is_scorable(&self) -> bool {
        !self.atoms.is_empty()
    }

    /// Parked-atom counts by code (receipt telemetry).
    #[must_use]
    pub fn parked_counts(&self) -> BTreeMap<String, usize> {
        let mut out = BTreeMap::new();
        for d in &self.dispositions {
            if let Err(code) = &d.outcome {
                *out.entry(code.code_name().to_string()).or_insert(0) += 1;
            }
        }
        out
    }
}
