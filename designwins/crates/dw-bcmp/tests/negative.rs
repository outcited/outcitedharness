//! Hermetic negative tests for the BCMP pilot (issue #29 contract).
//!
//! Every case builds a tiny fixture feed + fixture vault in a temp dir and
//! runs the real `run_batch`. No network, no real corpus.
//!
//! Test-module allowances (house pattern, HANDOFF §0): indexed fixture
//! access and `unwrap` are deliberate — a broken fixture must fail loudly,
//! not be shepherded into a pass.
#![allow(
    clippy::indexing_slicing,
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    reason = "fixture access in tests fails loudly by design"
)]

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use dw_bcmp::receipt::{run_batch, RunOpts};
use dw_bcmp::scenario::{validate, AtomProposal, GeneratorRef, ParkCode, Scenario};

fn tmp_dir(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!(
        "dw-bcmp-test-{tag}-{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos())
    ));
    fs::create_dir_all(&d).unwrap();
    d
}

fn feed_row(
    part: &str,
    mfr: &str,
    field: &str,
    value: &serde_json::Value,
    sha: &str,
    page: u32,
    verbatim: &str,
) -> String {
    serde_json::json!({
        "field": field,
        "value": value,
        "verbatim": verbatim,
        "unit": null,
        "document_sha256": sha,
        "page_1based": page,
        "manufacturer": mfr,
        "part_identity": part,
        "identity_status": "stamped",
        "_verify": {"verdict": "verified"},
        "confidence": 0.95,
        "method": "deterministic_pillar_v2",
    })
    .to_string()
}

fn write_feed(path: &Path, rows: &[String]) {
    fs::write(path, rows.join("\n") + "\n").unwrap();
}

fn make_vault(root: &Path, shas: &[&str]) {
    for sha in shas {
        let dir = root.join("cas").join(&sha[..2]);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join(format!("{sha}.pdf")), b"%PDF-fixture").unwrap();
    }
}

fn scenario(id: &str, atoms: Vec<AtomProposal>) -> Scenario {
    Scenario {
        scenario_id: id.into(),
        question: format!("question for {id}"),
        occasion: None,
        applications: vec![],
        proposed_atoms: atoms,
        generated_by: GeneratorRef {
            source: "hand".into(),
            model: None,
            endpoint: None,
            prompt_sha256: None,
        },
    }
}

fn atom(axis: &str, op: &str, value: f64) -> AtomProposal {
    AtomProposal {
        axis: axis.into(),
        op: op.into(),
        value: serde_json::Value::from(value),
        unit: None,
        hard: true,
    }
}

fn opts(job: &Path, feed: &Path, vault: &Path, max: usize) -> RunOpts {
    RunOpts {
        job_dir: job.to_path_buf(),
        feed: feed.to_path_buf(),
        vault_root: vault.to_path_buf(),
        repo_root: PathBuf::from("."),
        max_scenarios: max,
        evidence_cap: 4,
    }
}

fn write_scenarios(job: &Path, file: &str, scenarios: &[Scenario]) {
    fs::create_dir_all(job.join("scenarios")).unwrap();
    let body: String = scenarios
        .iter()
        .map(|s| serde_json::to_string(s).unwrap())
        .collect::<Vec<_>>()
        .join("\n");
    fs::write(job.join("scenarios").join(file), body + "\n").unwrap();
}

fn state_sha0(job: &Path) -> String {
    let state: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(job.join("state.json")).unwrap()).unwrap();
    state["atom_sets_done"][0].as_str().unwrap().to_string()
}

fn read_receipt(job: &Path, sha: &str) -> serde_json::Value {
    serde_json::from_str(
        &fs::read_to_string(job.join("receipts").join(format!("{sha}.json"))).unwrap(),
    )
    .unwrap()
}

const DOC_A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const DOC_B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const DOC_C: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";

fn base_fixture(tag: &str) -> (PathBuf, PathBuf, PathBuf) {
    let dir = tmp_dir(tag);
    let feed = dir.join("feed.jsonl");
    let vault = dir.join("vault");
    let job = dir.join("job");

    // ST part: 512 KB flash, 180 MHz, no CAN evidence at all.
    // TI part: 1024 KB flash, 120 MHz, 1 CAN.
    // NXP part: 64 KB flash only (fails >=128 hard, with evidence).
    // Renesas part: 512 KB flash but its document is NOT in the vault
    // (tampered/missing evidence -> must be Unknown, never eliminated).
    write_feed(
        &feed,
        &[
            feed_row(
                "stm32f103rb",
                "st",
                "flash_kb",
                &512.0.into(),
                DOC_A,
                3,
                "512 KB flash",
            ),
            feed_row(
                "stm32f103rb",
                "st",
                "clk_max_mhz",
                &72.0.into(),
                DOC_A,
                4,
                "72 MHz max",
            ),
            feed_row(
                "tm4c123gh",
                "texas_instruments",
                "flash_kb",
                &256.0.into(),
                DOC_B,
                2,
                "256KB flash",
            ),
            feed_row(
                "tm4c123gh",
                "texas_instruments",
                "n_can",
                &1.0.into(),
                DOC_B,
                5,
                "2x CAN",
            ),
            feed_row(
                "lpc1114fn",
                "nxp",
                "flash_kb",
                &32.0.into(),
                DOC_C,
                6,
                "32 kB flash",
            ),
            // Implausible claim (part number parsed as a count): must be refused.
            feed_row(
                "stm32f103rb",
                "st",
                "n_can",
                &734.0.into(),
                DOC_A,
                7,
                "NCV734x CAN",
            ),
            feed_row(
                "ra6m1ah",
                "renesas",
                "flash_kb",
                &512.0.into(),
                "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
                2,
                "512 KB",
            ),
        ],
    );
    make_vault(&vault, &[DOC_A, DOC_B, DOC_C]);
    (job, feed, vault)
}

#[test]
fn unsupported_and_undeclared_axes_are_parked_not_dropped() {
    // sram_kb: engine does not know it at all.
    let v = validate(scenario(
        "s1",
        vec![
            atom("sram_kb", "min_rating", 64.0),
            atom("temp_range_c", "covers", 85.0),
            atom("has_wireless_ble", "min_rating", 1.0),
        ],
    ));
    assert!(!v.is_scorable());
    let codes: Vec<&ParkCode> = v
        .dispositions
        .iter()
        .map(|d| d.outcome.as_ref().unwrap_err())
        .collect();
    assert_eq!(codes[0], &ParkCode::UnsupportedAxis);
    // temp_range_c exists in the engine but is not declared for this feed.
    assert_eq!(codes[1], &ParkCode::UndeclaredKnife);
    assert_eq!(codes[2], &ParkCode::UndeclaredKnife);
    assert_eq!(v.parked_counts().get("unsupported_axis"), Some(&1));
    assert_eq!(v.parked_counts().get("undeclared_knife"), Some(&2));
}

#[test]
fn wrong_binding_and_range_and_soft_are_parked() {
    let v = validate(scenario(
        "s2",
        vec![
            atom("flash_kb", "max_rating", 512.0), // wrong op
            atom("has_can", "min_rating", 20.0),   // out of band
            AtomProposal {
                hard: false,
                ..atom("flash_kb", "min_rating", 128.0)
            },
        ],
    ));
    let codes: Vec<&ParkCode> = v
        .dispositions
        .iter()
        .map(|d| d.outcome.as_ref().unwrap_err())
        .collect();
    assert_eq!(codes[0], &ParkCode::WrongBindingOp);
    assert_eq!(codes[1], &ParkCode::ValueOutOfRange);
    assert_eq!(codes[2], &ParkCode::SoftAtom);
    assert!(!v.is_scorable());
}

#[test]
fn missing_evidence_is_insufficient_never_eliminated() {
    let (job, feed, vault) = base_fixture("missing");
    // CAN atom: only TI has CAN evidence. ST has NO n_can claim (the 734
    // garbage is refused) -> ST must be insufficient_evidence, TI qualified.
    write_scenarios(
        &job,
        "s.jsonl",
        &[scenario("s3", vec![atom("has_can", "min_rating", 1.0)])],
    );
    let out = run_batch(&opts(&job, &feed, &vault, 0)).unwrap();
    assert_eq!(out.receipts_written, 1);

    let state = fs::read_to_string(job.join("state.json")).unwrap();
    let st: serde_json::Value = serde_json::from_str(&state).unwrap();
    let sha_list: Vec<String> = st["atom_sets_done"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let receipt = read_receipt(&job, &sha_list[0]);

    let brands = &receipt["brands"];
    assert_eq!(brands["st"]["state"], "insufficient_evidence");
    assert_eq!(brands["texas_instruments"]["state"], "qualified");
    // The refused garbage claim must NOT appear as a CAN elimination for ST.
    assert!(brands["st"]["eliminated_opns"]
        .as_array()
        .unwrap()
        .is_empty());
    // And the gap ledger must name CAN as missing for ST (1 OPN).
    assert_eq!(
        brands["st"]["unknown_axis_counts"]["has_can"].as_u64(),
        Some(1)
    );
}

#[test]
fn partial_unknown_survives_but_never_qualifies() {
    let (job, feed, vault) = base_fixture("partial");
    // Flash >=128 AND CAN: ST qualifies on flash but is Unknown on CAN ->
    // survives, not qualified; brand insufficient.
    write_scenarios(
        &job,
        "s.jsonl",
        &[scenario(
            "s4",
            vec![
                atom("flash_kb", "min_rating", 128.0),
                atom("has_can", "min_rating", 1.0),
            ],
        )],
    );
    let out = run_batch(&opts(&job, &feed, &vault, 0)).unwrap();
    assert_eq!(out.receipts_written, 1);
    let st: serde_json::Value = read_receipt(&job, &state_sha0(&job))["brands"]["st"].clone();
    assert_eq!(st["state"], "insufficient_evidence");
    assert!(st["qualified_opns"].as_array().unwrap().is_empty());
    // stm32f103rb survives (Unknown never eliminates) but is unqualified.
    assert!(st["surviving_unqualified_opns"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v == "stm32f103rb"));
}

#[test]
fn elimination_requires_evidence_and_names_the_knife() {
    let (job, feed, vault) = base_fixture("elim");
    // Flash >=128: NXP's 32 KB flash (verified, in vault) fails -> NXP
    // eliminated, with the killing knife and document evidence.
    // Renesas' doc is NOT in the vault -> Unknown -> NOT eliminated.
    write_scenarios(
        &job,
        "s.jsonl",
        &[scenario("s5", vec![atom("flash_kb", "min_rating", 128.0)])],
    );
    run_batch(&opts(&job, &feed, &vault, 0)).unwrap();
    let receipt = read_receipt(&job, &state_sha0(&job));
    let brands = &receipt["brands"];
    assert_eq!(brands["nxp"]["state"], "eliminated");
    let elim = &brands["nxp"]["eliminated_opns"][0];
    assert_eq!(elim["opn"], "lpc1114fn");
    assert_eq!(elim["axis"], "flash_kb");
    assert_eq!(elim["evidence"][0]["sha256"], DOC_C);
    assert_eq!(elim["evidence"][0]["page"], 6);
    assert_eq!(elim["verbatim"][0]["verbatim"], "32 kB flash");
    // Renesas: unresolvable evidence -> Unknown -> insufficient, not eliminated.
    assert_eq!(brands["renesas"]["state"], "insufficient_evidence");
    assert!(brands["renesas"]["eliminated_opns"]
        .as_array()
        .unwrap()
        .is_empty());
    // ST and TI qualify with evidence.
    assert_eq!(brands["st"]["state"], "qualified");
    assert_eq!(brands["texas_instruments"]["state"], "qualified");
}

#[test]
fn near_miss_survives_unqualified() {
    let (job, feed, vault) = base_fixture("nearmiss");
    // ST flash = 512, required 560: band 10% -> NearMiss (512 >= 504).
    write_scenarios(
        &job,
        "s.jsonl",
        &[scenario("s6", vec![atom("flash_kb", "min_rating", 560.0)])],
    );
    run_batch(&opts(&job, &feed, &vault, 0)).unwrap();
    let st = read_receipt(&job, &state_sha0(&job))["brands"]["st"].clone();
    assert_eq!(st["state"], "insufficient_evidence");
    assert!(st["surviving_unqualified_opns"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v == "stm32f103rb"));
    assert!(st["eliminated_opns"].as_array().unwrap().is_empty());
}

#[test]
fn unscorable_scenario_is_parked_whole() {
    let (job, feed, vault) = base_fixture("unscorable");
    write_scenarios(
        &job,
        "s.jsonl",
        &[scenario("s7", vec![atom("sram_kb", "min_rating", 64.0)])],
    );
    let out = run_batch(&opts(&job, &feed, &vault, 0)).unwrap();
    assert_eq!(out.receipts_written, 0);
    assert_eq!(out.scenarios_unscorable, 1);
    let parked = fs::read_to_string(job.join("parked.log.jsonl")).unwrap();
    assert!(parked.contains("no_scorable_atoms"));
}

#[test]
fn dedupe_one_receipt_per_atom_set_with_variants_kept() {
    let (job, feed, vault) = base_fixture("dedupe");
    let mut s1 = scenario("s8", vec![atom("flash_kb", "min_rating", 128.0)]);
    s1.question = "needs plenty of flash for a bootloader".into();
    let mut s2 = scenario("s9", vec![atom("flash_kb", "min_rating", 128.0)]);
    s2.question = "large firmware image, lots of code storage".into();
    // 128.0 vs 128 must normalize to the same set.
    let s3 = scenario("s10", vec![atom("flash_kb", "min_rating", 128.00001)]);
    write_scenarios(&job, "s.jsonl", &[s1, s2.clone(), s3]);
    let out = run_batch(&opts(&job, &feed, &vault, 0)).unwrap();
    assert_eq!(out.receipts_written, 1);
    // Different scenario ids, same canonical atom set: dedupe at the
    // receipt stage.
    assert_eq!(out.receipts_skipped_existing, 2);
    // The receipt keeps the FIRST question verbatim; variants travel in the
    // generator's variant log, not the receipt (assert the winner's text).
    let receipt = read_receipt(&job, &state_sha0(&job));
    assert_eq!(
        receipt["scenario"]["question"],
        "needs plenty of flash for a bootloader"
    );
    let _ = s2;
}

#[test]
fn restart_is_idempotent() {
    let (job, feed, vault) = base_fixture("restart");
    write_scenarios(
        &job,
        "s.jsonl",
        &[
            scenario("s11", vec![atom("flash_kb", "min_rating", 128.0)]),
            scenario("s12", vec![atom("freq_mhz", "min_rating", 80.0)]),
        ],
    );
    let first = run_batch(&opts(&job, &feed, &vault, 0)).unwrap();
    assert_eq!(first.receipts_written, 2);
    let before: BTreeMap<PathBuf, String> = fs::read_dir(job.join("receipts"))
        .unwrap()
        .filter_map(Result::ok)
        .map(|e| {
            let p = e.path();
            let body = fs::read_to_string(&p).unwrap();
            (p, body)
        })
        .collect();

    // Re-run: everything skipped (id dedupe fires first), nothing rewritten.
    let second = run_batch(&opts(&job, &feed, &vault, 0)).unwrap();
    assert_eq!(second.receipts_written, 0);
    assert_eq!(second.receipts_skipped_existing, 0);
    assert_eq!(second.duplicates_skipped, 2); // same scenario ids seen again

    let after: BTreeMap<PathBuf, String> = fs::read_dir(job.join("receipts"))
        .unwrap()
        .filter_map(Result::ok)
        .map(|e| {
            let p = e.path();
            let body = fs::read_to_string(&p).unwrap();
            (p, body)
        })
        .collect();
    assert_eq!(before, after, "receipt bytes must not change on rerun");
}

#[test]
fn changed_release_refuses_replay_required() {
    let (job, feed, vault) = base_fixture("release");
    write_scenarios(
        &job,
        "s.jsonl",
        &[scenario("s13", vec![atom("flash_kb", "min_rating", 128.0)])],
    );
    let first = run_batch(&opts(&job, &feed, &vault, 0)).unwrap();
    assert_eq!(first.receipts_written, 1);

    // Mutate the feed under the job.
    let mut rows: Vec<String> = fs::read_to_string(&feed)
        .unwrap()
        .lines()
        .map(str::to_string)
        .collect();
    rows.push(feed_row(
        "extra_part",
        "st",
        "flash_kb",
        &128.0.into(),
        DOC_A,
        9,
        "128 KB",
    ));
    write_feed(&feed, &rows);

    let second = run_batch(&opts(&job, &feed, &vault, 0)).unwrap();
    let err = second.error.expect("must refuse");
    assert!(err.contains("replay_required"), "got: {err}");
    assert_eq!(second.receipts_written, 0);
}

#[test]
fn stop_file_halts_early() {
    let (job, feed, vault) = base_fixture("stop");
    write_scenarios(
        &job,
        "s.jsonl",
        &[
            scenario("s14", vec![atom("flash_kb", "min_rating", 128.0)]),
            scenario("s15", vec![atom("freq_mhz", "min_rating", 80.0)]),
        ],
    );
    fs::write(job.join("STOP"), b"").unwrap();
    let out = run_batch(&opts(&job, &feed, &vault, 0)).unwrap();
    assert!(out.stopped_early);
    assert_eq!(out.receipts_written, 0);
}

#[test]
fn implausible_claim_values_are_refused_not_used() {
    // The 734-CAN garbage row must be refused by the loader, visible in meta.
    let (job, feed, _vault) = base_fixture("implausible");
    drop(job);
    let loaded = dw_bcmp::load_feed(&feed).unwrap();
    assert!(loaded.meta.rows_refused_implausible >= 1);
    // And no part carries a has_can axis from the garbage row.
    // TI legitimately carries 1 CAN; ST must not carry a CAN axis at all
    // (its only CAN-shaped claim, the refused 734, never became evidence).
    let st = loaded
        .facts
        .get(&dw_decide::Candidate {
            part_number: "stm32f103rb".into(),
            vendor: "st".into(),
        })
        .unwrap();
    assert!(!st.axes.contains_key(&dw_decide::Axis::HasCan));
}

#[test]
fn cohort_requires_mcu_markers() {
    // A part with only a CAN claim (a transceiver) must NOT be in the MCU
    // cohort.
    let dir = tmp_dir("cohort");
    let feed = dir.join("feed.jsonl");
    write_feed(
        &feed,
        &[
            feed_row(
                "ncv7342",
                "onsemi",
                "n_can",
                &1.0.into(),
                DOC_A,
                1,
                "CAN transceiver",
            ),
            feed_row(
                "stm32f103rb",
                "st",
                "flash_kb",
                &512.0.into(),
                DOC_A,
                3,
                "512 KB",
            ),
        ],
    );
    let loaded = dw_bcmp::load_feed(&feed).unwrap();
    assert_eq!(loaded.meta.cohort_parts, 1);
    assert_eq!(loaded.meta.parts_outside_cohort, 1);
    assert!(loaded
        .cohort
        .members
        .iter()
        .all(|c| c.part_number == "stm32f103rb"));
}

#[test]
fn receipt_quotes_the_winning_claim_not_a_superseded_one() {
    let dir = tmp_dir("winning");
    let feed = dir.join("feed.jsonl");
    let vault = dir.join("vault");
    let job = dir.join("job");
    // Two verified flash claims: 64 KB printed first, 512 KB later.
    // The value judged is 512 (best case); the receipt must quote the 512
    // claim, never the superseded 64.
    write_feed(
        &feed,
        &[
            feed_row(
                "p1",
                "st",
                "flash_kb",
                &64.0.into(),
                DOC_A,
                2,
                "64 KB flash",
            ),
            feed_row(
                "p1",
                "st",
                "flash_kb",
                &512.0.into(),
                DOC_A,
                9,
                "512 KB flash",
            ),
        ],
    );
    make_vault(&vault, &[DOC_A]);
    write_scenarios(
        &job,
        "s.jsonl",
        &[scenario("s20", vec![atom("flash_kb", "min_rating", 256.0)])],
    );
    run_batch(&opts(&job, &feed, &vault, 0)).unwrap();
    let receipt = read_receipt(&job, &state_sha0(&job));
    let qual = &receipt["brands"]["st"]["qualified_opns"][0];
    assert_eq!(qual["opn"], "p1");
    assert_eq!(qual["verbatim"]["flash_kb"][0]["verbatim"], "512 KB flash");
    assert_eq!(qual["verbatim"]["flash_kb"][0]["page"], 9);
}

#[test]
fn document_series_identities_are_refused_not_phantom_opns() {
    // Measured 2026-10-22: 231/1856 cohort entries were vendor doc ids
    // (NXP AN13224, ST RM0316…, onsemi and9097-d/ena2150-d, Espressif
    // …_datasheet slugs). A cohort member is buyable (CONTRACTS law 2).
    let dir = tmp_dir("docid");
    let feed = dir.join("feed.jsonl");
    write_feed(
        &feed,
        &[
            feed_row(
                "AN4340",
                "st",
                "flash_kb",
                &8.0.into(),
                DOC_A,
                1,
                "app note flash",
            ),
            feed_row(
                "rm0316",
                "st",
                "flash_kb",
                &256.0.into(),
                DOC_A,
                2,
                "RM table flash",
            ),
            feed_row(
                "AN13224",
                "nxp",
                "flash_kb",
                &128.0.into(),
                DOC_A,
                3,
                "AN flash",
            ),
            feed_row(
                "and9097-d",
                "onsemi",
                "flash_kb",
                &64.0.into(),
                DOC_A,
                4,
                "AND doc flash",
            ),
            feed_row(
                "ena2150-d",
                "onsemi",
                "flash_kb",
                &96.0.into(),
                DOC_A,
                5,
                "ENA doc flash",
            ),
            feed_row(
                "esp32-wrover-e_datasheet",
                "espressif",
                "flash_kb",
                &448.0.into(),
                DOC_A,
                6,
                "slug flash",
            ),
            feed_row(
                "stm32f103rb",
                "st",
                "flash_kb",
                &512.0.into(),
                DOC_A,
                7,
                "512 KB",
            ),
            feed_row(
                "lpc1114fn",
                "nxp",
                "flash_kb",
                &32.0.into(),
                DOC_A,
                8,
                "32 kB",
            ),
        ],
    );
    let loaded = dw_bcmp::load_feed(&feed).unwrap();
    let members: Vec<&str> = loaded
        .cohort
        .members
        .iter()
        .map(|c| c.part_number.as_str())
        .collect();
    assert_eq!(members, vec!["lpc1114fn", "stm32f103rb"]);
    assert_eq!(loaded.meta.rows_refused_doc_identity, 6);
}
