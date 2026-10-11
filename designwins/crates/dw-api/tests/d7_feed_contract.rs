//! The D7 delivery gate: every response renders the experience.
//!
//! Each gate here ships with its negative control — a body that is missing
//! exactly the thing the gate claims to enforce, which the audit must reject.
//! A test that cannot go red is not evidence (`docs/ASSURANCE.md`).

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::result_large_err,
    reason = "integration tests drive the public API and assert on JSON"
)]

use dw_api::feed::QueryResult;
use dw_api::store::{ApiStore, DemoStore};
use dw_api::{handle, parse_route, FeedAudit, Request, Response};

fn get(path: &str, store: &DemoStore) -> serde_json::Value {
    let req = Request {
        method: "GET".into(),
        path: path.into(),
        body: None,
    };
    handle(&req, store).expect("route handles").to_json()
}

fn post(
    path: &str,
    body: serde_json::Value,
    store: &DemoStore,
) -> Result<Response, dw_api::Problem> {
    let req = Request {
        method: "POST".into(),
        path: path.into(),
        body: Some(body),
    };
    handle(&req, store)
}

fn demo_release(store: &DemoStore) -> String {
    store.release_id().as_str().to_string()
}

/// The gate itself: every data route returns a body that passes the feed
/// audit — items 1–7 inline, evidence resolving, exclusions reasoned.
#[test]
fn every_data_response_carries_the_full_feed() {
    let store = DemoStore::new();
    let design_id = {
        let build = store.feed_build();
        build.id
    };
    let paths = [
        "/api/extraction/v1/parts/BU15TA2WHFV".to_string(),
        format!("/api/discovery/v1/designs/{design_id}"),
        "/api/pipeline/v1/jobs/job-42".to_string(),
        "/api/coverage/v1/cube".to_string(),
        "/api/coverage/v1/gaps".to_string(),
    ];
    for p in &paths {
        let body = get(p, &store);
        FeedAudit::of(&body).unwrap_or_else(|missing| {
            let names: Vec<_> = missing.iter().map(|m| m.0).collect();
            panic!(
                "{p} is not renderable; missing {names:?}\nbody keys: {:?}",
                body.as_object().map(|o| o.keys().collect::<Vec<_>>())
            );
        });
    }
    // The search route: the feed IS the response.
    let body = post(
        "/api/search/v1/query",
        serde_json::json!({"raw": "3.3v ldo", "release": demo_release(&store)}),
        &store,
    )
    .expect("query handles")
    .to_json();
    FeedAudit::of(&body).expect("query response is renderable");
}

/// Negative control: dropping `unknowns` must fail the audit. This is the
/// "UI infers certainty from absence" failure D7 names — if this passes, the
/// audit cannot fail on its core property.
#[test]
fn negative_control_dropping_a_feed_item_fails_the_audit() {
    let store = DemoStore::new();
    let body = get("/api/coverage/v1/cube", &store);
    assert!(FeedAudit::of(&body).is_ok());
    for key in [
        "unknowns", "next", "derived", "declared", "lineage", "evidence",
    ] {
        let mut stripped = body.clone();
        stripped.as_object_mut().unwrap().remove(key);
        let err = FeedAudit::of(&stripped).expect_err("stripped body must fail");
        assert!(
            err.iter().any(|m| m.0.contains(key)),
            "stripping `{key}` must be reported, got {err:?}"
        );
    }
}

/// Negative control: an evidence ref that resolves nowhere must fail —
/// "every number opens to the page" is the product's whole claim.
#[test]
fn negative_control_unresolvable_evidence_fails_the_audit() {
    let mut body = serde_json::json!({
        "release_id": "rel-x", "lineage": {"release_id": "rel-x", "parent": null,
            "engines": [], "rollback_to": null},
        "evidence": [{"field": "vout", "document_sha256": "a", "page": 1,
            "row_header": null, "column_header": null, "quote": null,
            "page_render": {"sha256": "a", "dpi": 144, "bbox": [0,0,0,0]},
            "crop": null, "crop_sha256": null}],
        "unknowns": [], "exclusions": [], "next": [], "declared": [], "derived": [],
        "cohort": {"members": []}, "explain": []
    });
    body["evidence"][0]["page_render"]
        .as_object_mut()
        .unwrap()
        .remove("href");
    let err = FeedAudit::of(&body).expect_err("unresolvable evidence must fail");
    assert!(err.iter().any(|m| m.0.contains("evidence")));
}

/// Negative control: an exclusion without a click-openable reason must fail.
#[test]
fn negative_control_unreasoned_exclusion_fails_the_audit() {
    let mut body = serde_json::json!({
        "release_id": "rel-x", "lineage": {"release_id": "rel-x", "parent": null,
            "engines": [], "rollback_to": null},
        "evidence": [], "unknowns": [], "next": [], "declared": [], "derived": [],
        "exclusions": [{"candidate": {"part_number": "X", "vendor": "Y"},
            "axis": "VinMinV", "ineligible": {"why": null}, "outcome": "fail"}],
        "cohort": {"members": []}, "explain": []
    });
    body["exclusions"][0]["ineligible"]
        .as_object_mut()
        .unwrap()
        .remove("why");
    let err = FeedAudit::of(&body).expect_err("unreasoned exclusion must fail");
    assert!(err.iter().any(|m| m.0.contains("exclusions")));
}

/// The wire law: an empty block is a statement. `unknowns: []` must be on the
/// wire even when nothing is unknown — a missing key is a lie.
#[test]
fn empty_blocks_are_still_on_the_wire() {
    let store = DemoStore::new();
    let body = get("/api/coverage/v1/gaps", &store);
    for key in [
        "unknowns",
        "next",
        "declared",
        "derived",
        "exclusions",
        "evidence",
    ] {
        let v = body
            .get(key)
            .unwrap_or_else(|| panic!("`{key}` missing entirely"));
        assert!(!v.is_null(), "`{key}` must be a statement, never null");
    }
}

/// `/healthz` is the one carve-out: it renders "the system is up", so it
/// carries release + lineage, and that is asserted — not the full feed.
#[test]
fn healthz_carries_release_and_lineage() {
    let store = DemoStore::new();
    let body = get("/healthz", &store);
    assert_eq!(body["status"], "ok");
    assert!(body.get("release_id").is_some());
    assert!(body
        .get("lineage")
        .is_some_and(|l| l.get("rollback_to").is_some()));
}

/// The search route runs the real funnel and pins the release.
#[test]
fn query_runs_the_funnel_and_pins_the_release() {
    let store = DemoStore::new();
    let body = post(
        "/api/search/v1/query",
        serde_json::json!({"raw": "3.3v ldo for a sensor", "release": demo_release(&store), "limit": 2}),
        &store,
    )
    .expect("query handles")
    .to_json();
    FeedAudit::of(&body).expect("renderable");
    assert_eq!(body["release_id"], demo_release(&store));
    assert_eq!(body["raw"], "3.3v ldo for a sensor");
    assert!(body["cohort"]["members"]
        .as_array()
        .is_some_and(|m| m.len() <= 2));
}

/// Asking for a different publication must be a renderable 404, not silence.
#[test]
fn wrong_release_is_a_renderable_problem() {
    let store = DemoStore::new();
    let err = post(
        "/api/search/v1/query",
        serde_json::json!({"raw": "x", "release": "rel-nosuch"}),
        &store,
    )
    .expect_err("unknown release must fail");
    assert_eq!(err.status, 404);
    let v = err.to_json();
    assert!(v
        .get("what_to_do")
        .is_some_and(|w| !w.as_str().unwrap_or("").is_empty()));
}

/// Filters are not silently ignored — they are honestly refused, and the
/// problem tells the caller what to do.
#[test]
fn filters_are_refused_not_ignored() {
    let store = DemoStore::new();
    let atom = dw_decide::Atom {
        knife: dw_decide::KnifeId::Axis(dw_decide::Axis::FlashKb),
        constraint: dw_decide::Constraint::Scalar {
            op: dw_decide::Op::MinRating,
            value: 64.0,
            margin_pct: None,
        },
        hard: true,
        required_conditions: std::collections::BTreeMap::new(),
    };
    let err = post(
        "/api/search/v1/query",
        serde_json::json!({
            "raw": "x", "release": demo_release(&store),
            "filters": [atom]
        }),
        &store,
    )
    .expect_err("filters must be refused");
    assert_eq!(err.status, 501, "problem: {}", err.detail);
    assert!(err.to_json().get("what_to_do").is_some());
}

/// Percent-encoded part numbers (family feed titles carry spaces and `™`)
/// decode before the lookup — the front end renders them from URLs.
#[test]
fn percent_encoded_part_numbers_resolve() {
    assert_eq!(
        dw_api::decode_segment("AURIX%E2%84%A2%20TC212"),
        "AURIX™ TC212"
    );
    assert_eq!(
        dw_api::decode_segment("plain"),
        "plain",
        "undecoded stays as-is"
    );
    let store = DemoStore::new();
    let req = Request {
        method: "GET".into(),
        path: "/api/extraction/v1/parts/BU15TA2WHFV%20x".into(),
        body: None,
    };
    let err = handle(&req, &store).expect_err("decoded name does not exist");
    assert!(
        err.detail.contains("BU15TA2WHFV x"),
        "the lookup sees the decoded name: {}",
        err.detail
    );
}

/// Unknown ids fail renderably; wrong methods say what is allowed.
#[test]
fn not_found_and_method_problems_are_renderable() {
    let store = DemoStore::new();
    let req = Request {
        method: "GET".into(),
        path: "/api/extraction/v1/parts/NOPE".into(),
        body: None,
    };
    let err = handle(&req, &store).expect_err("unknown part");
    assert_eq!(err.status, 404);
    assert!(err.to_json().get("what_to_do").is_some());

    let route = parse_route("DELETE", "/healthz").expect_err("wrong method");
    assert_eq!(route.status, 405);
    assert!(route.to_json().get("what_to_do").is_some());
}

/// The audit is a wire-shape gate, so it must accept exactly the JSON that
/// `QueryResult` produces — no drift between the two.
#[test]
fn audit_agrees_with_the_serializer() {
    let store = DemoStore::new();
    let build = store.feed_build();
    let result = QueryResult::from_build(build, store.lineage().clone(), vec![]);
    FeedAudit::of(&result.to_json()).expect("serializer output passes its own audit");
}
