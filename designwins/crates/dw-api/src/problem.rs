//! `application/problem+json` errors — **also renderable**.
//!
//! A failed request still has to tell a human what happened and what to do
//! (`C7`, `D7`). That is why every [`Problem`] carries `what_to_do` beside
//! `detail`: a machine-readable status is not an explanation.

use serde::Serialize;

/// A renderable failure. Serialised as `application/problem+json`.
///
/// Field `type` is renamed from `type_` because `type` is a Rust keyword; on
/// the wire it is `"type"` as RFC 7807 requires.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Problem {
    /// Problem type identifier. Stable, citable, human-readable.
    #[serde(rename = "type")]
    pub type_: String,
    /// Short headline a UI can show as-is.
    pub title: String,
    /// HTTP status code, mirrored in the body so a renderer has one source.
    pub status: u16,
    /// What happened, specifically.
    pub detail: String,
    /// Which request this is about (the path).
    pub instance: String,
    /// What the human should do next. Required — a `Problem` without it is
    /// not renderable and is unconstructible here.
    pub what_to_do: String,
}

impl Problem {
    /// Build a problem. `what_to_do` is required by construction — see the
    /// type's contract.
    #[must_use]
    pub fn new(
        type_: impl Into<String>,
        title: impl Into<String>,
        status: u16,
        detail: impl Into<String>,
        instance: impl Into<String>,
        what_to_do: impl Into<String>,
    ) -> Self {
        Self {
            type_: type_.into(),
            title: title.into(),
            status,
            detail: detail.into(),
            instance: instance.into(),
            what_to_do: what_to_do.into(),
        }
    }

    /// 404: the id does not resolve in this release.
    #[must_use]
    pub fn not_found(instance: impl Into<String>, what: &str, id: &str) -> Self {
        Self::new(
            "urn:dw:problem:not-found",
            "Not found",
            404,
            format!("{what} `{id}` does not resolve in this release"),
            instance,
            "Check the identifier, or ask for /api/coverage/v1/gaps to see what this release does contain",
        )
    }

    /// 400: the request is malformed or self-contradictory.
    #[must_use]
    pub fn bad_request(instance: impl Into<String>, detail: impl Into<String>) -> Self {
        Self::new(
            "urn:dw:problem:bad-request",
            "Bad request",
            400,
            detail,
            instance,
            "Fix the request shape; the expected schema is in docs/CONTRACTS.md C7",
        )
    }

    /// 401: missing or malformed credentials.
    #[must_use]
    pub fn unauthorized(instance: impl Into<String>) -> Self {
        Self::new(
            "urn:dw:problem:unauthorized",
            "Unauthorized",
            401,
            "this endpoint requires `Authorization: Bearer crk_…`",
            instance,
            "Send the API key as a Bearer token; keys are issued per front end",
        )
    }

    /// 405: known path, wrong method.
    #[must_use]
    pub fn method_not_allowed(instance: impl Into<String>, method: &str, allowed: &str) -> Self {
        Self::new(
            "urn:dw:problem:method-not-allowed",
            "Method not allowed",
            405,
            format!("`{method}` is not allowed on this route; allowed: {allowed}"),
            instance,
            "Use one of the allowed methods",
        )
    }

    /// 503: the store could not answer. The answer is not `Unknown` — the
    /// request itself failed.
    #[must_use]
    pub fn unavailable(instance: impl Into<String>, detail: impl Into<String>) -> Self {
        Self::new(
            "urn:dw:problem:unavailable",
            "Service unavailable",
            503,
            detail,
            instance,
            "Retry; if it persists, this release's store is not mounted",
        )
    }

    /// Serialise exactly as an RFC 7807 body.
    ///
    /// # Panics
    ///
    /// If serde fails on six strings and a `u16` — a derive bug, not a
    /// runtime condition.
    #[must_use]
    #[allow(
        clippy::expect_used,
        reason = "serde on this shape cannot fail; see # Panics"
    )]
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::to_value(self).expect("Problem serialises")
    }
}

#[cfg(test)]
#[allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    reason = "tests assert on their own JSON literals"
)]
mod tests {
    use super::*;

    /// Negative control: the renderable half of the contract. A problem that
    /// explains the failure but not the remedy must not be constructible —
    /// `what_to_do` is a required field with no default, and the JSON must
    /// carry it. If someone adds `#[serde(skip_serializing_if = ...)]` or a
    /// default here, this test goes red.
    #[test]
    fn every_problem_is_renderable() {
        let p = Problem::not_found("/api/discovery/v1/designs/x", "design", "x");
        let v = p.to_json();
        for key in [
            "type",
            "title",
            "status",
            "detail",
            "instance",
            "what_to_do",
        ] {
            assert!(v.get(key).is_some(), "problem JSON missing `{key}`: {v}");
            assert!(!v[key].is_null(), "problem JSON nulls `{key}`: {v}");
        }
        assert!(!p.what_to_do.trim().is_empty());
        assert_eq!(p.status, 404);
    }

    #[test]
    fn the_type_field_is_wire_named() {
        let v = Problem::bad_request("/x", "nope").to_json();
        assert_eq!(v["type"], "urn:dw:problem:bad-request");
    }
}
