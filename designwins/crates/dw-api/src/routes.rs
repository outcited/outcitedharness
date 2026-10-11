//! The seven routes, as pure functions over [`ApiStore`].
//!
//! ```text
//! GET  /healthz
//! GET  /api/extraction/v1/parts/{part_number}
//! GET  /api/discovery/v1/designs/{design_id}
//! GET  /api/pipeline/v1/jobs/{job_id}
//! POST /api/search/v1/query
//! GET  /api/coverage/v1/cube
//! GET  /api/coverage/v1/gaps
//! ```
//!
//! Every **data** response embeds the full feed block (flattened
//! [`QueryResult`]) because `D7` says every response carries items 1–7
//! inline. `/healthz` is the one carve-out: it renders *the system is up*,
//! not the experience, so it carries `release_id` + `lineage` only.

use serde::{Deserialize, Serialize};

use crate::feed::{Lineage, Query, QueryResult};
use crate::problem::Problem;
use crate::store::{ApiStore, CoverageCube, GapLedger, JobRecord, PartRecord};

/// A request as the handler sees it. The transport (HTTP in `dw-serve`)
/// builds this; tests build it directly.
#[derive(Debug, Clone, PartialEq)]
pub struct Request {
    /// `GET`, `POST`, …
    pub method: String,
    /// The path, without query string.
    pub path: String,
    /// Parsed JSON body, when there is one.
    pub body: Option<serde_json::Value>,
}

/// Which route a request is for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Route {
    /// Liveness + which release.
    Healthz,
    /// Extraction facade join, 1:1.
    Part(String),
    /// The discovery surface: a [`dw_decide::Build`] by id.
    Design(String),
    /// Pipeline job state.
    Job(String),
    /// The funnel, from one sentence.
    Query,
    /// Coverage cube slice.
    Cube,
    /// Gap ledger.
    Gaps,
}

/// Parse method + path into a [`Route`].
///
/// # Errors
///
/// Returns [`Problem::not_found`] for unknown paths and
/// [`Problem::method_not_allowed`] for a known path with the wrong method.
#[allow(
    clippy::result_large_err,
    reason = "Problem is the wire type; boxing it would leak into every call site"
)]
pub fn parse_route(method: &str, path: &str) -> Result<Route, Problem> {
    let m = method.to_ascii_uppercase();
    let known_wrong_method = |allowed: &str| -> Problem {
        Problem::method_not_allowed(path.to_string(), method, allowed)
    };
    match path {
        "/healthz" if m == "GET" => Ok(Route::Healthz),
        "/api/search/v1/query" if m == "POST" => Ok(Route::Query),
        "/api/coverage/v1/cube" if m == "GET" => Ok(Route::Cube),
        "/api/coverage/v1/gaps" if m == "GET" => Ok(Route::Gaps),
        "/healthz" | "/api/coverage/v1/cube" | "/api/coverage/v1/gaps" => {
            Err(known_wrong_method("GET"))
        }
        "/api/search/v1/query" => Err(known_wrong_method("POST")),
        _ => {
            let rest = path.strip_prefix("/api/extraction/v1/parts/");
            if let Some(id) = rest.filter(|s| !s.is_empty() && !s.contains('/')) {
                return if m == "GET" {
                    Ok(Route::Part(id.to_string()))
                } else {
                    Err(known_wrong_method("GET"))
                };
            }
            let rest = path.strip_prefix("/api/discovery/v1/designs/");
            if let Some(id) = rest.filter(|s| !s.is_empty() && !s.contains('/')) {
                return if m == "GET" {
                    Ok(Route::Design(id.to_string()))
                } else {
                    Err(known_wrong_method("GET"))
                };
            }
            let rest = path.strip_prefix("/api/pipeline/v1/jobs/");
            if let Some(id) = rest.filter(|s| !s.is_empty() && !s.contains('/')) {
                return if m == "GET" {
                    Ok(Route::Job(id.to_string()))
                } else {
                    Err(known_wrong_method("GET"))
                };
            }
            Err(Problem::not_found(path.to_string(), "route", path))
        }
    }
}

/// `/healthz` — the one response that is not the experience.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HealthzResponse {
    /// `"ok"`.
    pub status: String,
    /// Feed item 1: which truth this server is serving.
    pub release_id: dw_types::ReleaseId,
    /// Feed item 1: the lineage affordance.
    pub lineage: Lineage,
}

/// Extraction facade response: the feed, plus the part.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PartResponse {
    /// The seven-item feed block, flattened to the top level.
    #[serde(flatten)]
    pub feed: QueryResult,
    /// The part, joined 1:1.
    pub part: PartRecord,
}

/// Discovery response: the feed (which *is* the design), plus its id.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DesignResponse {
    /// The seven-item feed block.
    #[serde(flatten)]
    pub feed: QueryResult,
    /// Which design was requested.
    pub design_id: String,
}

/// Pipeline response: the feed, plus the job.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JobResponse {
    /// The seven-item feed block.
    #[serde(flatten)]
    pub feed: QueryResult,
    /// The job.
    pub job: JobRecord,
}

/// Coverage cube response: the feed, plus the cube.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CubeResponse {
    /// The seven-item feed block.
    #[serde(flatten)]
    pub feed: QueryResult,
    /// The cube slice.
    pub cube: CoverageCube,
}

/// Gap-ledger response: the feed, plus the gaps.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GapsResponse {
    /// The seven-item feed block.
    #[serde(flatten)]
    pub feed: QueryResult,
    /// The gaps.
    pub gaps: GapLedger,
}

/// A successful response, one arm per route.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Response {
    /// Liveness.
    Healthz(HealthzResponse),
    /// Part.
    Part(PartResponse),
    /// Design.
    Design(DesignResponse),
    /// Job.
    Job(JobResponse),
    /// Search: [`QueryResult`] is the whole response.
    Query(QueryResult),
    /// Cube.
    Cube(CubeResponse),
    /// Gaps.
    Gaps(GapsResponse),
}

impl Response {
    /// Serialise the body a front end receives.
    ///
    /// # Panics
    ///
    /// If serde fails on types made only of strings, numbers, and vectors —
    /// a derive bug, not a runtime condition.
    #[must_use]
    #[allow(
        clippy::expect_used,
        reason = "serde on this shape cannot fail; see # Panics"
    )]
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::to_value(self).expect("Response serialises")
    }
}

/// Evidence for every source a build's verdicts cite, so item 2 holds for
/// any feed built from that build.
fn evidence_for_build(
    store: &dyn ApiStore,
    build: &dw_decide::Build,
) -> Vec<crate::feed::EvidenceRef> {
    let mut out = Vec::new();
    for j in &build.explain {
        for s in &j.evidence {
            out.push(store.resolve_evidence(&format!("{:?}", j.axis), s));
        }
    }
    out
}

/// The feed block for a build: items 1–7 in one value.
fn feed_of(store: &dyn ApiStore, build: dw_decide::Build) -> QueryResult {
    let evidence = evidence_for_build(store, &build);
    QueryResult::from_build(build, store.lineage().clone(), evidence)
}

/// Route a request. Pure: same request + same store = same response.
///
/// # Errors
///
/// Returns a [`Problem`] for unknown routes, wrong methods, unknown ids,
/// malformed bodies, and release mismatches. Every one is renderable.
#[allow(
    clippy::result_large_err,
    reason = "Problem is the wire type; boxing it would leak into every call site"
)]
pub fn handle(req: &Request, store: &dyn ApiStore) -> Result<Response, Problem> {
    let route = parse_route(&req.method, &req.path)?;
    match route {
        Route::Healthz => Ok(Response::Healthz(HealthzResponse {
            status: "ok".into(),
            release_id: store.release_id().clone(),
            lineage: store.lineage().clone(),
        })),
        Route::Part(id) => {
            let part = store
                .part(&id)
                .ok_or_else(|| Problem::not_found(req.path.clone(), "part", &id))?;
            let build = store.feed_build();
            let mut feed = feed_of(store, build);
            // The part's own claims are the evidence viewer's subject.
            for c in &part.claims {
                feed.evidence
                    .push(store.resolve_evidence(&c.field, &c.source));
            }
            Ok(Response::Part(PartResponse { feed, part }))
        }
        Route::Design(id) => {
            let build = store
                .design(&id)
                .ok_or_else(|| Problem::not_found(req.path.clone(), "design", &id))?;
            Ok(Response::Design(DesignResponse {
                feed: feed_of(store, build),
                design_id: id,
            }))
        }
        Route::Job(id) => {
            let job = store
                .job(&id)
                .ok_or_else(|| Problem::not_found(req.path.clone(), "job", &id))?;
            Ok(Response::Job(JobResponse {
                feed: feed_of(store, store.feed_build()),
                job,
            }))
        }
        Route::Query => {
            let body = req
                .body
                .as_ref()
                .ok_or_else(|| Problem::bad_request(req.path.clone(), "a JSON body is required"))?;
            let query: Query = serde_json::from_value(body.clone()).map_err(|e| {
                Problem::bad_request(
                    req.path.clone(),
                    format!("body does not parse as Query: {e}"),
                )
            })?;
            if !query.filters.is_empty() {
                return Err(Problem::new(
                    "urn:dw:problem:not-implemented",
                    "Not implemented",
                    501,
                    "this release does not apply query filters yet",
                    req.path.clone(),
                    "Send the query without `filters`; the funnel derives its own atoms",
                ));
            }
            if query.release != *store.release_id() {
                return Err(Problem::not_found(
                    req.path.clone(),
                    "release",
                    query.release.as_str(),
                ));
            }
            let raw = match query.intent {
                Some(dw_decide::DesignIntent::Text(s)) => s,
                Some(other) => {
                    let detail = format!(
                        "intent variant {other:?} is not accepted by this release; send text"
                    );
                    return Err(Problem::bad_request(req.path.clone(), detail));
                }
                None => query.raw,
            };
            let mut build = store.search(&raw);
            if query.limit > 0 && build.cohort.members.len() > query.limit as usize {
                let kept: std::collections::BTreeSet<_> = build
                    .cohort
                    .members
                    .iter()
                    .take(query.limit as usize)
                    .cloned()
                    .collect();
                build.cohort.members.retain(|c| kept.contains(c));
                build.explain.retain(|j| kept.contains(&j.candidate));
                build.exclusions.retain(|e| kept.contains(&e.candidate));
            }
            Ok(Response::Query(feed_of(store, build)))
        }
        Route::Cube => Ok(Response::Cube(CubeResponse {
            feed: feed_of(store, store.feed_build()),
            cube: store.cube(),
        })),
        Route::Gaps => Ok(Response::Gaps(GapsResponse {
            feed: feed_of(store, store.feed_build()),
            gaps: store.gaps(),
        })),
    }
}
