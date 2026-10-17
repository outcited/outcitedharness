"""Universal project registry (§2) — durable registration, lifecycle,
and project-scoped identity.

Registry rows live in director.db (one registry per workspace; a workspace
hosts one or more projects). Project CONFIG lives in
<projects_root>/<project_id>/ (policy, deps, fleet, memory) and is
versioned + evidence-linked in the registration row.

Lifecycle: active | paused | archived (register/pause/resume/archive all
append registry events; engine behavior never changes).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .schema import fp, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_registry (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    objectives TEXT NOT NULL DEFAULT '[]',
    success_criteria TEXT NOT NULL DEFAULT '[]',
    repositories TEXT NOT NULL DEFAULT '[]',
    policy_version TEXT NOT NULL DEFAULT '',
    config_digest TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '[]',
    human_authority TEXT NOT NULL DEFAULT '',
    lifecycle TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS registry_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    ts REAL NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

LIFECYCLE = {"active", "paused", "archived"}


@dataclass
class ProjectSpec:
    project_id: str
    name: str
    description: str = ""
    objectives: list = field(default_factory=list)
    success_criteria: list = field(default_factory=list)
    repositories: list = field(default_factory=list)
    policy_version: str = ""
    config_digest: str = ""
    evidence: list = field(default_factory=list)
    human_authority: str = "sam"
    lifecycle: str = "active"
    shared_sources: list = field(default_factory=list)   # 05C §7: private
                                                         # by default


class ProjectRegistry:
    def __init__(self, con):
        self.con = con
        con.executescript(SCHEMA)

    def register(self, spec: ProjectSpec) -> ProjectSpec:
        if spec.lifecycle not in LIFECYCLE:
            raise ValueError(f"bad lifecycle {spec.lifecycle}")
        now = utcnow()
        self.con.execute(
            "INSERT OR REPLACE INTO project_registry(project_id, name, "
            "description, objectives, success_criteria, repositories, "
            "policy_version, config_digest, evidence, human_authority, "
            "lifecycle, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (spec.project_id, spec.name, spec.description,
             json.dumps(spec.objectives), json.dumps(spec.success_criteria),
             json.dumps(spec.repositories), spec.policy_version,
             spec.config_digest, json.dumps(spec.evidence),
             spec.human_authority, spec.lifecycle, now, now))
        self.con.execute(
            "INSERT INTO registry_events(project_id, ts, event, detail) "
            "VALUES (?,?,?,?)", (spec.project_id, now, "register",
                                 f"policy_version={spec.policy_version}"))
        self.con.commit()
        return spec

    def set_lifecycle(self, project_id: str, lifecycle: str,
                      detail: str = "") -> None:
        if lifecycle not in LIFECYCLE:
            raise ValueError(f"bad lifecycle {lifecycle}")
        self.con.execute(
            "UPDATE project_registry SET lifecycle=?, updated_at=? "
            "WHERE project_id=?", (lifecycle, utcnow(), project_id))
        self.con.execute(
            "INSERT INTO registry_events(project_id, ts, event, detail) "
            "VALUES (?,?,?,?)", (project_id, utcnow(), lifecycle, detail))
        self.con.commit()

    def get(self, project_id: str) -> Optional[ProjectSpec]:
        row = self.con.execute(
            "SELECT * FROM project_registry WHERE project_id=?",
            (project_id,)).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self.con.execute(
            "SELECT * FROM project_registry LIMIT 0").description]
        d = dict(zip(cols, row))
        return ProjectSpec(
            project_id=d["project_id"], name=d["name"],
            description=d["description"],
            objectives=json.loads(d["objectives"]),
            success_criteria=json.loads(d["success_criteria"]),
            repositories=json.loads(d["repositories"]),
            policy_version=d["policy_version"],
            config_digest=d["config_digest"],
            evidence=json.loads(d["evidence"]),
            human_authority=d["human_authority"], lifecycle=d["lifecycle"])

    def all(self) -> list[ProjectSpec]:
        ids = [r[0] for r in self.con.execute(
            "SELECT project_id FROM project_registry ORDER BY project_id")]
        return [self.get(i) for i in ids]


def load_spec_from_config(project_dir: Path) -> ProjectSpec:
    """projects/<id>/project.yaml -> ProjectSpec. Config digest is over
    the project's config files (versioned + evidence-linked)."""
    pdir = Path(project_dir)
    cfg = yaml.safe_load((pdir / "project.yaml").read_text()) \
        if (pdir / "project.yaml").exists() else {}
    digest_files = sorted(pdir.glob("*.yaml")) + sorted(pdir.glob("*.json"))
    digest = fp(*[f.name + f.read_text() for f in digest_files])[:16]
    return ProjectSpec(
        project_id=cfg.get("project_id", pdir.name),
        name=cfg.get("name", pdir.name),
        description=cfg.get("description", ""),
        objectives=cfg.get("objectives", []),
        success_criteria=cfg.get("success_criteria", []),
        repositories=cfg.get("repositories", []),
        policy_version=cfg.get("policy_version", ""),
        config_digest=digest,
        evidence=[f"config:{f.name}" for f in digest_files],
        human_authority=cfg.get("human_authority", "sam"),
        shared_sources=cfg.get("shared_sources", []))
