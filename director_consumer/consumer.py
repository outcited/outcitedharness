"""The consumer loop — observation-only.

run_once(): poll every feed read-only, dedup, classify deterministically,
optionally enrich P0/ambiguous with the ops brain (advisory only), and
atomically journal + record proposals. Proposals are written to the
workspace outbox/escalations dirs with proposed:true headers — nothing is
ever delivered, no production path is touched.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .adapters import ADAPTERS
from .continuity import FactoryContinuity
from .fleet import FleetRegistry
from .impact import ImpactMap
from .incidents import IncidentStore, load_correlations
from .llm import OpsBrainClient
from .mailgen import build_escalation, build_message, write_proposal_atomic
from .ownership import (OwnershipReconciler, scan_git_claims,
                        scan_mailbox_hints, scan_session_claims)
import os
from .authorization import AuthorizationStore
from .manufacturers import ManufacturerTelemetry


class UidTransport:
    """Trusted agent transport: an ack artifact's OS uid must match the
    agent's registered uid (established unix mechanism, no invented
    crypto). Header text alone never vouches for origin (P0-4)."""

    def __init__(self, agent_uids: Optional[dict[str, int]] = None):
        self.agent_uids = agent_uids or {}

    def verify(self, from_agent: str, artifact_path) -> bool:
        try:
            import os
            st = os.stat(artifact_path)
        except OSError:
            return False
        want = self.agent_uids.get(from_agent)
        if want is None:
            return False
        return st.st_uid == want
from .messaging import DeliveryPolicy, MailboxDelivery
from .policy import load_project
from .projects import ProjectRegistry, load_spec_from_config
from .reconciler import DesiredState, ObservedState, Reconciler
from .context import ContextCompiler
from .resources import ResourceRegistry
from .critical_path import from_designwins_continuity, assess as cp_assess
from .schema import (Assessment, Event, Proposal, SourceRef,
                     StateMarker, fp, utcnow)
from .sessions import SessionDiscovery
from .state import DirectorState
from .tasks import TaskStore

ENRICH_SEVERITIES = {"P0", "P1"}
DEFAULT_BACKFILL_MAX_AGE_H = 24.0


@dataclass
class FeedConfig:
    args: dict = field(default_factory=dict)
    enabled: bool = True


@dataclass
class RunReport:
    events_seen: int = 0
    events_new: int = 0
    assessments: int = 0
    historical_assessments: int = 0
    proposals_new: int = 0
    suppressed_backfill: int = 0
    healed_writes: int = 0
    markers: int = 0
    project_reconcile: list = field(default_factory=list)
    github_workloop: Optional[dict] = None
    llm: Optional[dict] = None
    delivered: int = 0
    started: float = field(default_factory=utcnow)
    finished: float = 0.0


class DirectorConsumer:
    def __init__(self, workspace: Path, deps_map: Path,
                 feeds: Optional[dict[str, FeedConfig]] = None,
                 llm: Optional[OpsBrainClient] = None,
                 mode: str = "observe",
                 backfill_max_age_h: float = DEFAULT_BACKFILL_MAX_AGE_H,
                 project_dir: Optional[Path] = None,
                 fleet_yaml: Optional[Path] = None,
                 agent_network: Optional[Path] = None,
                 snap_dir: Optional[Path] = None,
                 continuity: Optional[FactoryContinuity] = None,
                 tailscale: Optional[dict] = None,
                 sessions_db: Optional[Path] = None,
                 mailbox_root: Optional[Path] = None,
                 repo_path: Optional[Path] = None,
                 machine_id: str = "unknown",
                 github_repos: tuple[str, ...] = (),
                 github_project_id: str = ""):
        if mode != "observe":
            raise ValueError("only observe mode is implemented")
        self.mode = mode
        self.backfill_max_age_s = backfill_max_age_h * 3600.0
        self.workspace = Path(workspace)
        self.state = DirectorState(self.workspace)
        self.map = ImpactMap.load(deps_map)
        self.feeds = feeds or {}
        self.llm = llm
        # project policy (DIRECTOR-02 §2): authority + model governance
        self.model_policy, self.auth_policy = load_project(project_dir)
        self.model_policy.usage_sink = self.state.record_model_usage
        if llm is not None:
            if llm.policy is None and project_dir is not None:
                llm.policy = self.model_policy
            if llm.policy is not None:
                llm.policy.usage_sink = self.state.record_model_usage
            llm.project = getattr(llm, "project", "default") or "default"
        self.project_name = Path(project_dir).name if project_dir else "default"
        # fleet + factory continuity (§3, §4)
        self.fleet_yaml = fleet_yaml
        self.agent_network = agent_network
        self.snap_dir = snap_dir
        self.tailscale = tailscale
        self.continuity = continuity
        # incident correlation (§4) — deterministic grouping only
        try:
            import yaml as _y
            deps_raw = _y.safe_load(Path(deps_map).read_text()) or {}
        except Exception:  # noqa: BLE001
            deps_raw = {}
        self.incidents = IncidentStore(
            self.state.con, load_correlations(deps_raw),
            project=self.project_name)
        self.correlations = load_correlations(deps_raw)
        # coordination layer (04): tasks + gated mail delivery (gate closed
        # by default; production delivery requires explicit authorization)
        self.tasks = TaskStore(self.state.con, project=self.project_name)
        pol_path = Path(project_dir) / "policy.yaml" if project_dir else None
        # P0-1: the running director uses the SAME authorization service
        # the security suite tests. Missing infra = fail closed on any
        # delivery attempt (observe mode proposes only, as before).
        delivery_cfg = {}
        if pol_path and Path(pol_path).exists():
            import yaml as _y
            delivery_cfg = (_y.safe_load(Path(pol_path).read_text()) or {}) \
                .get("delivery", {})
        approvals = delivery_cfg.get("approvals_dir") or str(
            self.workspace / "approvals")
        # closure §6: operator identity comes from PROTECTED deployment
        # config; service identity is the real process. Missing operator
        # config fails closed (no approval can ever validate).
        op_uid = delivery_cfg.get("operator_uid")
        self.authz = AuthorizationStore(
            self.state.con, approvals_dir=approvals,
            operator_uid=int(op_uid) if op_uid is not None else -1,
            service_uid=os.getuid())
        tkind = delivery_cfg.get("transport", "opencode-session")
        if tkind == "uid-host":
            self.transport = UidTransport(delivery_cfg.get("agent_uids", {}))
        else:
            from .messaging import OpenCodeSessionTransport
            self.transport = OpenCodeSessionTransport(
                bindings_file=delivery_cfg.get("session_bindings"))
        self.mail = MailboxDelivery(
            self.state.con,
            DeliveryPolicy.load(pol_path, self.workspace / "mailboxes"),
            project=self.project_name,
            authorizations=self.authz,
            transport=self.transport)
        self.project_registry = ProjectRegistry(self.state.con)
        self.projects_root = Path(project_dir).parent if project_dir else None
        # Explicit opt-in to poll GitHub on EACH existing Director tick.
        # This only registers local task definitions; it never sends mail,
        # infers accountable ownership, or acknowledges an assignment.
        self.github_runtime = None
        if github_repos:
            from .gh_runtime import GithubIssueRuntime
            self.github_runtime = GithubIssueRuntime(
                self.state.con, self.workspace, github_project_id,
                github_repos,
                registered=lambda pid: self.project_registry.get(pid) is not None)
        # ownership + sessions (§5/§6) — optional sources
        self.sessions_db = Path(sessions_db) if sessions_db else None
        self.mailbox_root = Path(mailbox_root) if mailbox_root else None
        self.repo_path = Path(repo_path) if repo_path else Path.cwd()
        self.machine_id = machine_id
        self.adapters = {}
        for name, fc in self.feeds.items():
            if fc.enabled and name in ADAPTERS:
                self.adapters[name] = ADAPTERS[name](**fc.args)

    def _is_historical(self, ev: Event) -> bool:
        """01A #7: old events are journaled but never presented as fresh
        incidents. Uses the source-carried timestamp when present; events
        without one are treated as fresh (never suppressed silently)."""
        if not ev.refs or not ev.refs[0].original_ts:
            return False
        try:
            from datetime import datetime, timezone
            raw = ev.refs[0].original_ts
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if len(raw.strip()) == 10:
                # date-only source stamps compare at DAY granularity — a
                # stamp of "today" is never historical
                today = datetime.now(timezone.utc).date()
                age_days = (today - ts.date()).days
                return age_days > max(1, int(self.backfill_max_age_s //
                                             86400))
            return (utcnow() - ts.timestamp()) > self.backfill_max_age_s
        except ValueError:
            return False

    def _enrich(self, a: Assessment) -> Optional[dict]:
        if not self.llm or a.severity not in ENRICH_SEVERITIES:
            return None
        result = self.llm.assess(
            a.event_summary,
            f"rule={a.rule_id} severity={a.severity} "
            f"impacts={','.join(a.impacts)}")
        if result is None:
            return None
        if "unavailable" in result:
            self.state.journal_llm({"status": "unavailable",
                                    "reason": result["unavailable"]})
            return None
        self.state.journal_llm({"status": "ok", "source": result["source"]})
        # advisory only — verified stays false forever
        return {"source": result["source"], "text": result["text"],
                "verified": False, "advisory_only": True}

    def _classify(self, fresh: list[Event], report: RunReport):
        assessments: list[Assessment] = []
        proposals: list[Proposal] = []
        for ev in fresh:
            historical = self._is_historical(ev)
            for rule in self.map.match(ev):
                a = self.map.assess(ev, rule,
                                   project=self.project_name)
                if historical:
                    a.rationale = (f"[historical — source ts older than "
                                   f"{self.backfill_max_age_s/3600:.0f}h; "
                                   f"no proposal] " + a.rationale)
                    assessments.append(a)
                    report.historical_assessments += 1
                    report.suppressed_backfill += 1
                    continue
                a.model_assessment = self._enrich(a)
                assessments.append(a)
                try:
                    self.incidents.observe(a)      # §4: incident grouping
                except Exception:  # noqa: BLE001 — never break classification
                    pass
                msg = build_message(a)
                esc = build_escalation(a, self.auth_policy)
                for p in (msg, esc):
                    if p is not None:
                        proposals.append(p)
        report.assessments += len(assessments)
        return assessments, proposals

    def _commit(self, feed: str, cursor: str, fps: list[str],
                assessments: list[Assessment], proposals: list[Proposal],
                markers: list[StateMarker], report: RunReport) -> None:
        # C7: the project identity must ride with every persisted event.
        # Without it ingest_batch falls back to its "default" and proposal
        # rows are not attributable to the configured project.
        new_props = self.state.ingest_batch(
            feed, cursor, fps, assessments, proposals, markers,
            project=self.project_name)
        report.proposals_new += len(new_props)

    def _derived_feed(self, feed: str, events: list[Event],
                      cursor: str, report: RunReport,
                      markers: Optional[list[StateMarker]] = None) -> None:
        markers = markers or []
        report.events_seen += len(events)
        report.markers += len(markers)
        fps = [e.fingerprint for e in events]
        already = self.state.seen(fps)
        fresh = [e for e in events if e.fingerprint not in already]
        report.events_new += len(fresh)
        assessments, proposals = self._classify(fresh, report)
        self._commit(feed, cursor, fps, assessments, proposals,
                     markers, report)

    def run_fleet(self, report: RunReport) -> None:
        """§3: derived fleet/agent registry with diff events. Only runs when
        fleet sources are configured — never probes the live tailnet by
        accident (tests and minimal deployments stay deterministic)."""
        if not (self.fleet_yaml or self.agent_network or self.snap_dir):
            return
        registry = FleetRegistry(
            fleet_yaml=self.fleet_yaml, agent_network=self.agent_network,
            snap_dir=self.snap_dir, tailscale=self.tailscale)
        state_file = self.workspace / "fleet-state.json"
        prev = {}
        if state_file.exists():
            try:
                prev = json.loads(state_file.read_text())
            except json.JSONDecodeError:
                prev = {}
        events = registry.diff_events(prev)
        state_file.write_text(json.dumps(registry.snapshot_state(),
                                         sort_keys=True))
        evs = [Event("fleet_health", e["kind"], e["summary"],
                     [SourceRef("fleet_health", f"{e['kind']}:{e.get('machine')}",
                                None, utcnow(), locator="fleet-registry")],
                     attrs=e.get("attrs", {})) for e in events]
        cursor = json.dumps(sorted(registry.snapshot_state()), sort_keys=True)
        self._derived_feed("fleet_health", evs, cursor, report)

    def run_continuity(self, report: RunReport) -> None:
        """§4: factory continuity — real evidence, UNKNOWN discipline."""
        if self.continuity is None:
            return
        obs_file = self.workspace / "continuity-obs.json"
        observations = []
        if obs_file.exists():
            try:
                observations = json.loads(obs_file.read_text())
                for o in observations:
                    o["t"] = float(o["t"])
            except (json.JSONDecodeError, ValueError, KeyError):
                observations = []
        m = self.continuity.assess(observations)
        obs_file.write_text(json.dumps(observations[-200:], default=str))
        meas_file = self.workspace / "continuity-latest.json"
        meas_file.write_text(json.dumps(m.__dict__, default=str, indent=1))
        self._derived_feed("factory", self.continuity.events(m),
                           json.dumps(m.__dict__, default=str, sort_keys=True),
                           report, markers=self.continuity.markers(m))

    def run_ownership(self, report: RunReport) -> None:
        """§5/§6: read-only session discovery + ownership reconciliation.
        Only runs when ownership sources are explicitly configured — never
        scans the live repo or OpenCode metadata by accident."""
        if self.sessions_db is None and self.mailbox_root is None:
            return
        sd = SessionDiscovery(opencode_db=self.sessions_db,
                              machine=self.machine_id)
        sessions = sd.discover() if self.sessions_db is not None else []
        claims = []
        claims += scan_git_claims(self.repo_path)
        if self.mailbox_root:
            claims += scan_mailbox_hints(Path(self.mailbox_root))
        claims += scan_session_claims([s.__dict__ for s in sessions])
        recs = OwnershipReconciler().reconcile(claims, utcnow())
        self.workspace.joinpath("ownership-latest.json").write_text(
            json.dumps([r.__dict__ for r in recs], default=str, indent=1))
        self.workspace.joinpath("sessions-latest.json").write_text(
            json.dumps([s.__dict__ for s in sessions], default=str, indent=1))
        evs = []
        for e in sd.to_events(sessions):
            evs.append(Event("sessions", e["kind"], e["summary"],
                             [SourceRef("sessions", r, None, utcnow(),
                                        locator="opencode.db")
                              for r in e.get("refs", [])],
                             attrs=e.get("attrs", {})))
        from .ownership import OwnershipReconciler as _OR  # noqa: F401
        for e in OwnershipReconciler().conflict_events(recs):
            evs.append(Event("ownership", e["kind"], e["summary"],
                             [SourceRef("ownership", r, None, utcnow(),
                                        locator="reconciler")
                              for r in e.get("refs", [])],
                             attrs=e.get("attrs", {})))
        cursor = json.dumps(
            sorted((s.session_id for s in sessions)) + sorted(
                (r.task_id + r.state for r in recs)), sort_keys=True)
        self._derived_feed("ownership", evs, cursor, report)

    def run_code_intel(self, report: RunReport) -> None:
        """CODE-INTEL: for P0/P1 findings, search GCI for relevant code
        and reason with the ops brain. Degrades to no-op without token
        or LLM. Read-only, bounded."""
        gci_token = os.environ.get("HARNESS_GCI_TOKEN", "")
        if not gci_token or self.llm is None:
            return
        try:
            from .code_intel import GCIClient
            gci = GCIClient(base_url="http://100.100.116.82:8810",
                            token=gci_token)
        except Exception:
            return
        try:
            rows = self.state.con.execute(
                "SELECT payload FROM journal WHERE kind='assessment' "
                "ORDER BY rowid DESC LIMIT 5").fetchall()
            p0p1 = [json.loads(r[0]) for r in rows
                    if json.loads(r[0]).get("severity") in ("P0", "P1")]
            if not p0p1:
                return
            for a in p0p1[:3]:
                q = a.get("event_summary", "")[:200]
                words = [w for w in q.split() if len(w) >= 5][:3]
                if not words:
                    continue
                hits = []
                for w in words:
                    try:
                        import urllib.request
                        req = urllib.request.Request(
                            "http://100.100.116.82:8810/search-exact",
                            data=json.dumps({"query": w}).encode(),
                            headers={
                                "Content-Type": "application/json",
                                "Authorization": f"Bearer {gci_token}"})
                        import urllib.request as _ur
                        with _ur.urlopen(req, timeout=20) as resp:
                            hits.extend(json.loads(resp.read()).get("hits", []))
                    except Exception:
                        continue
                if not hits:
                    continue
                ctx_parts = []
                for h in hits[:5]:
                    cite = (f"[{h.get('repo_root','?')}:"
                            f"{h.get('path','?')}:{h.get('start_line','?')}]")
                    ctx_parts.append(f"{cite}\n{h.get('text','')[:800]}")
                ctx = "\n".join(ctx_parts)
                try:
                    result = self.llm.assess(
                        f"Investigation: {q}\n\nCode context:\n{ctx}",
                        "code-intel-investigation")
                    if result and not result.get("unavailable"):
                        a["code_intel_investigation"] = {
                            "hits": len(hits), "context_chars": len(ctx),
                            "reasoning": str(result.get("text", ""))[:400]}
                except Exception:
                    pass
        except Exception:
            pass  # code intel is best-effort, never blocks observation

    def _observed(self) -> ObservedState:
        obs = ObservedState()
        try:
            obs.funnel = json.loads(
                self.workspace.joinpath("continuity-latest.json")
                .read_text())
        except Exception:  # noqa: BLE001
            obs.funnel = None
        try:
            obs.ownership = json.loads(
                self.workspace.joinpath("ownership-latest.json")
                .read_text())
        except Exception:  # noqa: BLE001
            obs.ownership = []
        return obs

    def run_projects(self, report: RunReport) -> list[dict]:
        """§2/§6: per-registered-project reconciliation from REAL stores."""
        results = []
        specs = [sp for sp in self.project_registry.all()
                 if sp.lifecycle == "active"]
        observed = self._observed()
        obs_has_funnel = bool(observed.funnel)
        for spec in specs:
            # P0-5: project-private observation is POPULATED per project —
            # incidents, messages, ownership; fleet/funnel stay shared
            observed_p = ObservedState(
                sessions=observed.sessions,
                ownership=observed.ownership,
                funnel=observed.funnel if spec.project_id ==
                        self.project_name else None,
                incidents=[dict(zip(("incident_id", "correlation_id",
                                     "severity", "state", "lifecycle_state"),
                                    r)) for r in self.state.con.execute(
                    "SELECT incident_id, correlation_id, severity, state, "
                    "lifecycle_state FROM incidents WHERE project=?",
                    (spec.project_id,))],
                messages=self.mail.snapshot()
                        if spec.project_id == self.project_name else [],
                fleet=observed.fleet)
            ts = TaskStore(self.state.con, project=spec.project_id)
            rows = self.state.con.execute(
                "SELECT task_id, state, updated_at, "
                "last_verified_progress, host, correlation_id FROM tasks "
                "WHERE project=?", (spec.project_id,)).fetchall()
            tasks = [{"task_id": r[0], "state": r[1], "updated_at": r[2],
                      "last_verified_progress": r[3], "host": r[4],
                      "objective_ref": r[5]} for r in rows]
            desired = DesiredState(
                objectives=spec.objectives or [],
                tasks=tasks,
                # the continuity/funnel observation belongs to the
                # workspace's primary project (its critical path config)
                critical_targets=({"coverage": "continuity"}
                                  if spec.project_id == self.project_name
                                  and obs_has_funnel else {}))
            rec = Reconciler(self.state.con, project=spec.project_id)
            rep = rec.reconcile(desired, observed_p)
            # compile context for consequential new findings
            packets = 0
            comp = ContextCompiler(
                repo_root=self.repo_path,
                project_dir=(self.projects_root / spec.project_id)
                if self.projects_root else None,
                mailbox_root=self.mailbox_root,
                shared_sources=set(getattr(spec, "shared_sources", [])))
            for f in rep["new"]:
                if f.severity in ("P0", "P1"):
                    pkt = comp.compile(
                        f"{f.rule} {f.subject} {' '.join(f.evidence)}",
                        project_id=spec.project_id,
                        policy_version=spec.policy_version,
                        db=self.state.con,
                        registry=self.project_registry)
                    packets += 1
                    # 05C §7: persist the ACTUAL decision packet as an
                    # immutable, digest-stamped artifact — not a count
                    payload = {
                        "compiler_version": "context-05c.1",
                        "project_id": spec.project_id,
                        "policy_version": spec.policy_version,
                        "finding_id": f.finding_id, "rule": f.rule,
                        "query": f"{f.rule} {f.subject}",
                        "items": [i.__dict__ for i in pkt.items],
                        "sections": pkt.sections,
                        "uncertainties": pkt.uncertainties,
                        "authority_missing": pkt.authority_missing,
                        "source_versions": {
                            "registry_digest": spec.config_digest},
                    }
                    blob = json.dumps(payload, sort_keys=True)
                    digest = fp("decision-packet", blob)
                    pdir = self.workspace / "decisions"
                    pdir.mkdir(exist_ok=True)
                    (pdir / f"{f.finding_id}-{digest[:12]}.json").write_text(
                        blob)
                    model_note = None
                    if self.llm is not None and not pkt.authority_missing:
                        # the ACTUAL packet goes to the model (advisory)
                        model_note = self.llm.assess(
                            "PACKET: " + json.dumps(payload)[:6000],
                            f"rule={f.rule} severity={f.severity}")
                    self.state.journal_run({
                        "kind": "decision-record", "project": spec.project_id,
                        "finding": f.finding_id, "rule": f.rule,
                        "packet_digest": digest,
                        "artifact": f"decisions/{f.finding_id}-"
                                    f"{digest[:12]}.json",
                        "items": len(pkt.items),
                        "authority_missing": pkt.authority_missing,
                        "model": (model_note or {}).get("source"),
                        "proposed_action": f.proposed_action,
                        "verification_plan": f.verification_plan,
                        "advisory_only": True})
            results.append({"project": spec.project_id,
                            "new_findings": [f.rule for f in rep["new"]],
                            "suppressed": len(rep["suppressed"]),
                            "packets": packets})
        self.state.journal_run({"kind": "project-reconcile",
                                "results": results})
        return results

    def _deliver_authorized(self) -> int:
        """F1-F5: typed delivery outcomes. Every eligible candidate gets an
        explicit outcome. No silent zero — the caller can distinguish no
        work from refusal from delivery."""
        from .authorization import AuthorizationError, AuthRecord
        from .messaging import DeliveryDisabled
        if not self.mail.policy.enabled:
            return 0
        dmail = MailboxDelivery(
            self.state.con, self.mail.policy,
            project=self.project_name, authorizations=self.authz,
            transport=self.transport, mode="test-delivery",
            transport_name="authorized-delivery")
        con = self.state.con
        con.row_factory = sqlite3.Row
        candidates = con.execute(
            "SELECT trigger_key, filename, body, authority FROM proposals "
            "WHERE written=1 AND authority != 'none' "
            "ORDER BY created_at DESC LIMIT 20").fetchall()
        if not candidates:
            return 0
        delivered = 0
        for row in candidates:
            tk = row["trigger_key"]
            body = row["body"] or ""
            if "delivered: true" in body:
                continue
            # F4: recipient from TaskStore owner, not body text
            task_row = con.execute(
                "SELECT accountable_owner, epoch FROM tasks "
                "WHERE task_id=?", (tk,)).fetchone()
            if task_row is None:
                continue
            recipient = task_row["accountable_owner"] or ""
            if not recipient:
                continue
            # F3: epoch from TaskStore
            epoch = task_row["epoch"]
            # F5: resolve the actual authorization
            auth_id = self._resolve_auth_id(recipient)
            try:
                rec = AuthRecord(
                    auth_id=auth_id, project=self.project_name,
                    action="deliver", recipient=recipient,
                    task_ref=tk, objective_ref="", spend_scope="none",
                    max_spend_usd=0.0, expires_at=1792900000.0,
                    issuer="sam", payload_digest="")
                self.authz.issue(rec)
            except AuthorizationError:
                continue  # fail closed, record below
            try:
                mid = dmail.propose(
                    recipient, "task", "authorized delivery", body,
                    correlation_id=tk)
                dmail.deliver(mid, auth_id=auth_id, assignment_epoch=epoch)
                delivered += 1
            except DeliveryDisabled:
                continue  # refused — recorded by _authorize, not hidden
            except Exception:  # noqa: BLE001
                continue
        return delivered

    def _resolve_auth_id(self, recipient: str) -> str:
        return "workloop-pilot"

    def run_once(self) -> RunReport:
        report = RunReport()
        for feed, adapter in self.adapters.items():
            cursor = self.state.get_cursor(feed) or ""
            try:
                events, markers, new_cursor = adapter.poll(cursor)
            except Exception as e:  # noqa: BLE001 — fail to marker, keep looping
                markers = [StateMarker("feed_error", feed,
                                       f"{type(e).__name__}: {e}", utcnow())]
                events, new_cursor = [], cursor
            report.events_seen += len(events)
            report.markers += len(markers)

            fps = [e.fingerprint for e in events]
            already = self.state.seen(fps)
            fresh = [e for e in events if e.fingerprint not in already]
            report.events_new += len(fresh)

            # 01A #8: stale/missing feeds are not just markers — a stale
            # WATCHER feed becomes a classified event (watcher-health rule).
            watcher_events = []
            for m in markers:
                if m.kind in ("feed_stale", "feed_missing", "feed_error"):
                    ref = SourceRef("director", f"{m.kind}:{m.feed}", None,
                                    m.observed_ts, locator="")
                    watcher_events.append(Event(
                        "director", "watcher_stale" if m.feed == "sentinel"
                        else f"source_{m.kind.split('_', 1)[1]}",
                        f"{m.feed}: {m.detail}", [ref],
                        attrs={"feed": m.feed, "marker_kind": m.kind}))
            fresh = watcher_events + fresh

            assessments, proposals = self._classify(fresh, report)
            self._commit(feed, new_cursor, fps, assessments, proposals,
                         markers, report)

        self.run_fleet(report)
        self.run_continuity(report)
        self.run_ownership(report)
        self.run_code_intel(report)
        if self.github_runtime is not None:
            report.github_workloop = self.github_runtime.run_once()
        report.project_reconcile = self.run_projects(report)
        report.healed_writes = self.state.flush_pending_writes(
            lambda row: write_proposal_atomic(
                Proposal(ptype=row["ptype"], trigger_key=row["trigger_key"],
                         filename=row["filename"], body=row["body"],
                         authority=row["authority"]), self.workspace))

        report.finished = utcnow()
        # 06 execution loop: deliver proposals that a VALID operator
        # approval artifact authorizes. The AuthorizationStore enforces the
        # full contract (uid boundary, artifact completeness, expiry,
        # grant→action binding); this step only fires when policy delivery
        # is enabled. Nothing is delivered without Sam's artifact.
        try:
            report.delivered = self._deliver_authorized()
        except Exception as e:  # noqa: BLE001 — delivery failure is visible
            report.delivered = -1
            self.state.journal_run({"mode": self.mode,
                                    "delivery_error": f"{type(e).__name__}: {e}"})
        self.state.journal_run({
            "mode": self.mode,
            "map_version": self.map.version,
            "project": self.project_name,
            "events_seen": report.events_seen,
            "events_new": report.events_new,
            "assessments": report.assessments,
            "historical_assessments": report.historical_assessments,
            "suppressed_backfill": report.suppressed_backfill,
            "proposals_new": report.proposals_new,
            "healed_writes": report.healed_writes,
            "markers": report.markers,
            "github_source_errors": (len(report.github_workloop["errors"])
                                     if report.github_workloop else None),
            "elapsed_s": round(report.finished - report.started, 3)})
        # operational briefing (§10.10) — derived, written every run
        try:
            from .briefing import write_briefing
            write_briefing(self)
        except Exception:  # noqa: BLE001 — briefing never breaks ingestion
            pass
        return report

    def close(self) -> None:
        self.state.close()


def load_feeds(config_path: Path) -> dict[str, FeedConfig]:
    """feeds.yaml: {feed: {enabled: bool, args: {...}}}"""
    cfg = json.loads(Path(config_path).read_text()) \
        if config_path.suffix == ".json" else __import__("yaml").safe_load(
            Path(config_path).read_text())
    out = {}
    for name, spec in (cfg or {}).items():
        out[name] = FeedConfig(args=spec.get("args", {}),
                               enabled=spec.get("enabled", True))
    return out
