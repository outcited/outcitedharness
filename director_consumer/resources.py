"""Universal resource registry + placement recommendations (§7).

Machines are RESOURCES, not organizational roles. Project assignments are
data. Placement is RECOMMENDATION ONLY — the director never moves
workloads or alters reservations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

UNKNOWN = "unknown"


@dataclass
class ResourceRecord:
    resource_id: str
    kind: str = "machine"                 # machine | gpu | queue | endpoint
    capabilities: dict = field(default_factory=dict)
    reservations: dict = field(default_factory=dict)   # project -> detail
    availability: str = "unknown"         # available|reserved|draining|
                                          # maintenance|unavailable|unknown
    freshness: str = "unknown"
    constraints: list = field(default_factory=list)
    project_assignments: list = field(default_factory=list)
    health: dict = field(default_factory=dict)


class ResourceRegistry:
    def __init__(self):
        self.resources: dict[str, ResourceRecord] = {}

    def upsert(self, rec: ResourceRecord) -> None:
        self.resources[rec.resource_id] = rec

    @classmethod
    def from_fleet(cls, fleet_registry,
                   project_of_role: Optional[dict] = None) -> "ResourceRegistry":
        """Derive resources from a FleetRegistry (03/02). Role strings map
        to project assignments via config — never hardcoded."""
        reg = cls()
        for mid in fleet_registry.machines:
            h = fleet_registry.machine_health(mid)
            roles = h.get("roles", [])
            assigned = sorted({
                (project_of_role or {}).get(r, "shared")
                for r in roles} - {None})
            status = h.get("status", "unknown")
            avail = {"active": "available", "planned": "unavailable",
                     "migrating": "available",
                     "draining": "draining"}.get(status, "unknown")
            if h.get("verdict") == "service-degraded" and \
                    avail == "available":
                avail = "available"      # capacity may exist; health separate
            reg.upsert(ResourceRecord(
                resource_id=mid, kind="machine",
                capabilities={"roles": roles,
                              "connectivity": h.get("connectivity")},
                reservations={},
                availability=avail,
                freshness=h.get("last_observed", "unknown"),
                project_assignments=assigned,
                health={"verdict": h.get("verdict"),
                        "services": h.get("services", {}),
                        "migration": h.get("migration", "")}))
        return reg

    def recommend_placement(self, requirements: dict) -> list[dict]:
        """Recommendation only. requirements: {capability: str, project:
        str, avoid_hosts: [...], prefer: [...]}. Returns ranked candidates
        with reasons; NEVER mutates anything."""
        want_cap = requirements.get("capability", "")
        project = requirements.get("project", "")
        avoid = set(requirements.get("avoid_hosts", []))
        prefer = set(requirements.get("prefer", []))
        out = []
        for r in self.resources.values():
            if r.resource_id in avoid:
                continue
            if r.availability in ("unavailable", "maintenance"):
                continue
            # §7: offline, unknown-health, or stale telemetry is never
            # execution-ready — config `active` does not override evidence
            if r.capabilities.get("connectivity") == "offline":
                continue
            if r.health.get("services", {}).get("_telemetry") == "STALE":
                continue
            if r.health.get("verdict") == UNKNOWN and \
                    not r.health.get("migration"):
                continue
            roles = r.capabilities.get("roles", [])
            match = (not want_cap) or any(want_cap in role for role in roles)
            if not match:
                continue
            score = 0
            reasons = []
            if project and project in r.project_assignments:
                score += 2
                reasons.append(f"already assigned to {project}")
            if r.availability == "available":
                score += 2
                reasons.append("available")
            if r.availability == "draining":
                score -= 2
                reasons.append("draining (not recommended)")
            if r.resource_id in prefer:
                score += 1
                reasons.append("preferred by config")
            if r.health.get("verdict") == UNKNOWN and \
                    r.health.get("migration"):
                score += 1
                reasons.append("migration target (declared)")
            out.append({"resource_id": r.resource_id, "score": score,
                        "reasons": reasons,
                        "verdict": r.health.get("verdict"),
                        "recommendation_only": True})
        out.sort(key=lambda x: -x["score"])
        return out
