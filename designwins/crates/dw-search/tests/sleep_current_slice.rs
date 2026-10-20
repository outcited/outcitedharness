//! The sleep-current vertical slice — one sentence to defensible verdicts
//! on REAL manufacturer rows.
//!
//! Substrate: `tests/fixtures/sleep_mode_rows_v1.jsonl` — five actual rows
//! from the 2026-10-19 shared-parse burn (`harness.electronics-mode-
//! currents.v1`), exported verbatim from `lane-out/burn/mode.s0.jsonl`.
//! Every part identity, sha, page, unit and role below is manufacturer
//! printed evidence, not synthetic.
//!
//! The ruling under test (2026-10-19): for an at-most consumption
//! requirement, a guaranteed MAXIMUM (ceiling) proves satisfaction when
//! under budget and NEVER proves violation; a guaranteed MINIMUM (floor)
//! above budget proves violation; typical decides nothing; missing
//! provenance decides nothing.

#![allow(clippy::expect_used, clippy::unwrap_used, clippy::indexing_slicing)]

use std::path::PathBuf;

use dw_decide::{judge, Axis, Verdict};
use dw_knives::seed_from_measured_chips;
use dw_search::mode_rows::{population_from_mode_rows, ModeRow, RowsProvenance};

fn fixture() -> Vec<ModeRow> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/sleep_mode_rows_v1.jsonl");
    std::fs::read_to_string(path)
        .expect("fixture present")
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).expect("row parses"))
        .collect()
}

/// The engineer's sentence, verbatim.
const SENTENCE: &str = "I need an MCU that sleeps under 2 µA";

#[test]
fn journey_sentence_to_verdicts_on_real_rows() {
    let rows = fixture();
    let built = population_from_mode_rows(&rows);
    let provenance = RowsProvenance::from_rows(&rows);

    // 1. The phrase layer binds the sentence to the sleep-current axis with
    //    the budget taken FROM the sentence (2 µA -> 2e-6 A).
    let registry = seed_from_measured_chips();
    let bound = registry.bind(SENTENCE);
    assert!(
        bound.atoms.iter().any(|a| matches!(
            a.knife,
            dw_decide::KnifeId::Axis(Axis::SleepCurrentA)
        )),
        "sentence must bind the sleep-current axis"
    );
    let sleep_atom = bound
        .atoms
        .iter()
        .find(|a| matches!(a.knife, dw_decide::KnifeId::Axis(Axis::SleepCurrentA)))
        .unwrap()
        .clone();

    // 2. Verdicts per candidate — the ruling table, on real rows.
    let mut verdicts = std::collections::BTreeMap::new();
    for (candidate, facts) in &built.population.facts {
        let evidence = facts.axes.get(&Axis::SleepCurrentA);
        let v = judge(evidence, &sleep_atom, &provenance);
        verdicts.insert(candidate.part_number.clone(), (v, evidence));
    }

    // 87c1fef2…: printed max 0.2 µA (guaranteed ceiling UNDER budget) — PASS.
    let pass_part = verdicts
        .iter()
        .find(|(p, _)| p.starts_with("STM32H747"))
        .map(|(_, (v, _))| *v)
        .expect("ceiling-under candidate present");
    assert_eq!(pass_part, Verdict::Pass, "guaranteed max 0.2 µA satisfies ≤ 2 µA");

    // 8a4fee15…: printed max 5.2 µA (ceiling OVER budget) — UNKNOWN, not
    // FAIL. The manufacturer permits up to 5.2 µA; that does not prove the
    // part draws more than 2 µA. This is the heart of the ruling.
    let over_part = verdicts
        .iter()
        .find(|(p, _)| p.starts_with("TM4C1233"))
        .map(|(_, (v, _))| *v)
        .expect("ceiling-over candidate present");
    assert_ne!(over_part, Verdict::Fail, "a ceiling above budget never eliminates");
    assert_eq!(over_part, Verdict::Unknown, "ceiling 5.2 µA vs 2 µA budget = Unknown");

    // 31106cf7…: printed MIN 7.6 mA (guaranteed floor ABOVE budget) — FAIL.
    // Even at its best, the part draws more than the budget: violation proven.
    let floor_part = verdicts
        .iter()
        .find(|(p, _)| p.starts_with("TMS320F280015x"))
        .map(|(_, (v, _))| *v)
        .expect("floor-over candidate present");
    assert_eq!(
        floor_part,
        Verdict::Fail,
        "lowest guaranteed floor 10 µA (0.01 mA) > 2 µA across the FULL mode inventory"
    );

    // Identity law: candidates are parts, never shas.
    for part in verdicts.keys() {
        assert!(
            !part.chars().take(16).all(|c| c.is_ascii_hexdigit()),
            "candidate {part} looks like a sha — identity law violated"
        );
    }

    // 3. The per-MHz row was refused with a reason — a different quantity.
    assert!(
        built.skipped.iter().any(|(m, s)| matches!(
            s,
            dw_search::mode_rows::ModeSkip::UnitNotApplicable(u)
                if u.contains("MHz") || m.contains("Sleep")
        )),
        "per-MHz row must be skipped, not mixed: {:?}",
        built.skipped
    );

    // 4. Evidence carries provenance: every judged candidate opens to a
    //    real (sha, page, row).
    for (_part, (_v, ev)) in &verdicts {
        if let Some(e) = ev {
            assert!(
                e.sources.iter().all(|s| s.page >= 1),
                "sources must carry a real page"
            );
        }
    }
}

/// The numberless sentence does not fire and lands in residue — D5 turns
/// it into the threshold question instead of inventing a budget.
#[test]
fn sentence_without_number_falls_to_residue() {
    let registry = seed_from_measured_chips();
    let bound = registry.bind("I need an MCU that sleeps");
    assert!(
        !bound
            .atoms
            .iter()
            .any(|a| matches!(a.knife, dw_decide::KnifeId::Axis(Axis::SleepCurrentA))),
        "no number, no atom"
    );
    assert!(
        bound.unmapped.iter().any(|u| u.contains("sleep")),
        "the sleep concept stays visible as residue: {:?}",
        bound.unmapped
    );
}

/// Tampered provenance: a source that does not resolve decides nothing
/// (C9.1 — a number we cannot open is a number we cannot assert).
#[test]
fn tampered_provenance_yields_unknown() {
    use dw_decide::{Bound, Evidence};
    use dw_types::{Sha256, SourceRef};

    let rows = fixture();
    let built = population_from_mode_rows(&rows);
    let provenance = RowsProvenance::from_rows(&rows);
    let registry = seed_from_measured_chips();
    let sleep_atom = registry
        .bind(SENTENCE)
        .atoms
        .into_iter()
        .find(|a| matches!(a.knife, dw_decide::KnifeId::Axis(Axis::SleepCurrentA)))
        .unwrap();

    // The same guaranteed 0.2 µA ceiling, but sourced from a sha/page/row
    // combination that exists nowhere: it must not decide.
    let forged = Evidence::measured_bound(
        0.2e-6,
        Bound::Ceiling,
        true,
        vec![SourceRef {
            sha256: Sha256::new("ab".repeat(32)).unwrap(),
            page: 99,
            row_header: Some("Hibernate (forged)".into()),
            column_header: Some("max".into()),
            crop: None,
        }],
    );
    let v = judge(Some(&forged), &sleep_atom, &provenance);
    assert_eq!(v, Verdict::Unknown, "unresolvable provenance decides nothing");

    // Control: the real 0.2 µA ceiling still passes through the same
    // resolver — proving it was the provenance, not the value.
    let real = built
        .population
        .facts
        .values()
        .find_map(|f| f.axes.get(&Axis::SleepCurrentA).cloned())
        .unwrap();
    let v2 = judge(Some(&real), &sleep_atom, &provenance);
    assert_ne!(v2, Verdict::Unknown, "real provenance resolves");
}

/// Typical never decides: a candidate whose only row is `typ` carries a
/// Point with no operating guarantee — Unknown under an at-most budget,
/// even when the typical value is comfortably under it (1.23 µA < 2 µA).
#[test]
fn typical_value_never_proves_pass() {
    use dw_decide::{Bound, Evidence};
    let rows = fixture();
    let built = population_from_mode_rows(&rows);
    let provenance = RowsProvenance::from_rows(&rows);
    let registry = seed_from_measured_chips();
    let sleep_atom = registry
        .bind(SENTENCE)
        .atoms
        .into_iter()
        .find(|a| matches!(a.knife, dw_decide::KnifeId::Axis(Axis::SleepCurrentA)))
        .unwrap();

    // Find the real typ row's part and rebuild it as the sole evidence.
    let typ_row = rows
        .iter()
        .find(|r| r.value_role.as_deref() == Some("typ"))
        .expect("typ row in fixture");
    let typ_evidence = Evidence::measured_bound(
        typ_row.value * 1e-6,
        Bound::Point,
        false, // typ: rating does not allow an operating guarantee (C9.5)
        vec![dw_types::SourceRef {
            sha256: dw_types::Sha256::new(typ_row.pdf_sha.clone().unwrap()).unwrap(),
            page: typ_row.page_1based.unwrap_or(1),
            row_header: Some(typ_row.mode.clone()),
            column_header: Some("typ".into()),
            crop: None,
        }],
    );
    let v = judge(Some(&typ_evidence), &sleep_atom, &provenance);
    assert_eq!(
        v,
        Verdict::Unknown,
        "typical 1.23 µA supports discovery, never a guaranteed pass"
    );
}
