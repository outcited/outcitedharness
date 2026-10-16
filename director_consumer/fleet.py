"""Fleet + agent registry — derived, never declared as truth.

Sources (all read-only, per §3):
  - Tailscale status --json      -> L3 connectivity (NEVER service health)
  - fleet_state snapshots         -> service endpoint + container state
  - AGENT_NETWORK.md              -> agent identities / machines
  - project fleet.yaml            -> declared roles/capabilities/assignments
                                    (provenance='config', operational facts
                                    and migration targets, not health)

Laws:
  - machine != agent != session != workload != owner
  - Tailscale online is NOT service health; service health comes only from
    endpoint telemetry (snapshot) and is UNKNOWN when telemetry is missing
  - every field carries provenance + observed time; stale sources yield
    UNKNOWN, never a guess
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

UNKNOWN = "unknown"
HEALTHY_STATES = {"READY", "ALIVE", "OK", "IDENTICAL"}
DOWN_STATES = {"UNREACHABLE", "SSH_FAIL", "NO_CONTAINERS", "TIMEOUT",
               "EMPTY", "HTTP_5"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class MachineRecord:
    machine_id: str
    aliases: list[str] = field(default_factory=list)
    tailscale_ips: list[str] = field(default_factory=list)
    connectivity: str = UNKNOWN          # online|offline|unknown (L3 only)
    os: str = UNKNOWN
    services: dict[str, str] = field(default_factory=dict)  # name -> state|unknown
    containers: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    status: str = UNKNOWN                # active|migrating|planned|unknown
    capability: dict[str, Any] = field(default_factory=dict)
    migration: str = ""
    provenance: dict[str, str] = field(default_factory=dict)  # field -> src
    last_observed: str = ""


@dataclass
class AgentRecord:
    agent_id: str
    machine_id: str
    kind: str = UNKNOWN     # human-interactive|autonomous-worker|director-managed-future
    note: str = ""
    provenance: str = ""


def tailscale_status(binary: str = "tailscale") -> Optional[dict]:
    """Best-effort; None when the tailnet cannot be queried (registry then
    reports connectivity UNKNOWN — never fabricates offline)."""
    for cand in (binary, "/opt/homebrew/bin/tailscale",
                 "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        try:
            r = subprocess.run([cand, "status", "--json"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return json.loads(r.stdout)
        except Exception:  # noqa: BLE001
            continue
    return None


def latest_snapshot(snap_dir: Path,
                    max_age_s: float = 26 * 3600) -> Optional[dict]:
    """Returns the newest snapshot, or None when absent OR STALE (§7:
    stale service telemetry yields UNKNOWN — never healthy). The dict is
    stamped with _freshness for the registry to surface."""
    if not snap_dir.exists():
        return None
    files = sorted(snap_dir.glob("*.json"))
    if not files:
        return None
    try:
        data = json.loads(files[-1].read_text())
    except json.JSONDecodeError:
        return None
    ts = data.get("timestamp")
    try:
        from datetime import datetime, timezone
        snap_ts = datetime.fromisoformat(
            str(ts).replace("Z", "+00:00")).timestamp()
        age = time.time() - snap_ts
    except (ValueError, TypeError):
        age = None
    if age is not None and age > max_age_s:
        data = dict(data)
        data["_stale"] = True
        data["_age_h"] = round(age / 3600, 1)
    return data


def parse_agent_network(path: Path) -> list[AgentRecord]:
    """Identities table rows: | `id` | desc | machine-ish | tree | ... —
    we take identity + the tree/machine column as evidence-level hints; the
    authoritative machine mapping lives in project fleet.yaml."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(errors="replace").splitlines():
        m = re.match(r"\|\s*`([a-z0-9_-]+)`\s*\|", line)
        if m:
            out.append(AgentRecord(agent_id=m.group(1), machine_id=UNKNOWN,
                                   kind=UNKNOWN,
                                   provenance=f"agent_network:{path.name}"))
    return out


class FleetRegistry:
    def __init__(self, fleet_yaml: Optional[Path] = None,
                 agent_network: Optional[Path] = None,
                 snap_dir: Optional[Path] = None,
                 tailscale: Optional[dict] = None,
                 aliases: Optional[dict[str, str]] = None):
        self.machines: dict[str, MachineRecord] = {}
        self.agents: list[AgentRecord] = []
        self.source_notes: dict[str, str] = {}
        if aliases is not None:
            self.aliases = dict(aliases)
        else:
            from .adapters import load_default_aliases
            self.aliases = load_default_aliases()
        self._build(fleet_yaml, agent_network, snap_dir, tailscale)

    # ---- build ----

    def _canon(self, name: str) -> str:
        n = name.lower().split(".")[0]
        return self.aliases.get(name, self.aliases.get(n, n))

    def _build(self, fleet_yaml, agent_network, snap_dir, tailscale):
        ts = tailscale if tailscale is not None else tailscale_status()
        if ts is None:
            self.source_notes["tailscale"] = (
                "UNAVAILABLE — connectivity reported UNKNOWN (§3: never "
                "fabricate offline from a lost probe)")
        snap = latest_snapshot(Path(snap_dir)) if snap_dir else None
        stale_snap = bool(snap and snap.get("_stale"))
        if snap is None:
            self.source_notes["fleet_snapshot"] = (
                "no snapshot — service health UNKNOWN")
        elif stale_snap:
            self.source_notes["fleet_snapshot"] = (
                f"STALE snapshot {snap.get('timestamp')} "
                f"({snap.get('_age_h')}h old) — service health UNKNOWN, "
                f"never healthy-by-default (§7)")
        else:
            self.source_notes["fleet_snapshot"] = (
                f"snapshot {snap.get('timestamp')}")

        cfg = {}
        if fleet_yaml and Path(fleet_yaml).exists():
            cfg = yaml.safe_load(Path(fleet_yaml).read_text()) or {}
        aliases: dict[str, str] = dict(self.aliases)
        for mid, spec in (cfg.get("machines") or {}).items():
            rec = MachineRecord(machine_id=mid,
                                roles=list(spec.get("roles", [])),
                                status=spec.get("status", UNKNOWN),
                                capability=dict(spec.get("capability", {})),
                                migration=spec.get("migration", ""),
                                provenance={"roles": "config:fleet.yaml"})
            self.machines[mid] = rec
            aliases[mid] = mid
        self.aliases = {**aliases, **self.aliases}

        # tailscale identity (connectivity only). Self is the local machine —
        # resolved to its canonical id so 'localhost' duplicates never appear.
        if ts:
            self_node = ts.get("Self") or {}
            self_host = (self_node.get("HostName") or "").lower()
            self_ips = self_node.get("TailscaleIPs") or []
            skip_names = {"localhost", "127.0.0.1", ""}
            for peer in (ts.get("Peer") or {}).values():
                host = (peer.get("HostName") or "").lower()
                if host in skip_names:
                    continue
                mid = self._canon(host)
                rec = self.machines.get(mid)
                if rec is None:
                    rec = MachineRecord(machine_id=mid)
                    self.machines[mid] = rec
                rec.aliases.append(host)
                rec.tailscale_ips = peer.get("TailscaleIPs") or []
                rec.connectivity = "online" if peer.get("Online") else "offline"
                rec.os = peer.get("OS", UNKNOWN)
                rec.provenance["connectivity"] = "tailscale"
                rec.last_observed = _now_iso()
            if self_host and self_host not in skip_names:
                mid = self._canon(self_host)
                rec = self.machines.get(mid) or MachineRecord(machine_id=mid)
                self.machines.setdefault(mid, rec)
                rec.tailscale_ips = self_ips or rec.tailscale_ips
                rec.connectivity = "online"
                rec.provenance["connectivity"] = "tailscale:self"
                rec.last_observed = _now_iso()

        # snapshot service state — STALE snapshots mark services UNKNOWN
        if snap and not stale_snap:
            for box, containers in (snap.get("nodes") or {}).items():
                mid = self._canon(box)
                rec = self.machines.get(mid)
                if rec is None:
                    rec = MachineRecord(machine_id=mid)
                    self.machines[mid] = rec
                rec.containers = list(containers)
                rec.provenance["containers"] = "fleet_state:snapshot"
                rec.last_observed = rec.last_observed or _now_iso()
            for name, states in (snap.get("endpoints") or {}).items():
                state = states[0] if states else UNKNOWN
                target = self._canon(name.split("_")[0])
                rec = self.machines.get(target)
                if rec is not None:
                    rec.services[name] = state
                    rec.provenance[f"service:{name}"] = "fleet_state:snapshot"
        elif stale_snap:
            for mid in self.machines:
                self.machines[mid].services.setdefault("_telemetry", "STALE")

        # agents
        for a in parse_agent_network(Path(agent_network)) if agent_network else []:
            self.agents.append(a)
        for aid, spec in (cfg.get("agents") or {}).items():
            declared = AgentRecord(
                agent_id=aid, machine_id=spec.get("machine", UNKNOWN),
                kind=spec.get("kind", UNKNOWN), note=spec.get("note", ""),
                provenance="config:fleet.yaml")
            self.agents = [a for a in self.agents if a.agent_id != aid]
            self.agents.append(declared)

    # ---- derived views ----

    def machine_health(self, mid: str) -> dict:
        """Health layers kept strictly separate (§3/§7). Stale telemetry
        never yields healthy; planned/migrating states are surfaced."""
        rec = self.machines.get(mid)
        if rec is None:
            return {"machine": mid, "connectivity": UNKNOWN,
                    "services": {}, "verdict": UNKNOWN,
                    "note": "machine not observed in any source"}
        stale = rec.services.get("_telemetry") == "STALE"
        svc_down = [n for n, s in rec.services.items()
                    if n != "_telemetry" and any(d in s.upper()
                                                 for d in DOWN_STATES)]
        svc_known = bool([n for n in rec.services if n != "_telemetry"])
        if stale or not svc_known:
            verdict = UNKNOWN                 # no/fresh telemetry — never guess
        elif svc_down:
            verdict = "service-degraded"
        else:
            verdict = "healthy"
        status = rec.status
        if status in ("planned", "migrating", "draining", "unavailable",
                      "degraded"):
            verdict = status if verdict == UNKNOWN and status != "active" \
                else verdict
        return {"machine": mid, "connectivity": rec.connectivity,
                "services": dict(rec.services), "containers": rec.containers,
                "verdict": verdict,
                "online_is_not_healthy": rec.connectivity == "online"
                and verdict in (UNKNOWN, "service-degraded"),
                "roles": rec.roles, "status": status,
                "migration": rec.migration[:120] if rec.migration else "",
                "last_observed": rec.last_observed}

    def diff_events(self, prev: Optional[dict]) -> list[dict]:
        """Registry diff -> feed events (fleet_health). A machine going
        offline is NEVER inferred from service state, and vice versa.
        First observation of an ALREADY-broken condition is an event
        (discovering a pre-existing problem is worth one incident)."""
        cur = {mid: self.machine_health(mid) for mid in self.machines}
        events = []
        prev = prev or {}
        for mid, c in cur.items():
            p = prev.get(mid, {})
            was_conn = p.get("connectivity")
            if c["connectivity"] == "offline" and was_conn != "offline":
                # first sight of an already-offline box, or online->offline
                events.append({"kind": "machine_offline", "machine": mid,
                               "summary": f"{mid} connectivity "
                                          f"{was_conn or UNKNOWN} -> offline",
                               "attrs": {"box": mid}})
            elif c["connectivity"] == "online" and was_conn == "offline":
                events.append({"kind": "machine_online", "machine": mid,
                               "summary": f"{mid} connectivity offline -> online",
                               "attrs": {"box": mid}})
            for svc, st in c["services"].items():
                was = (p.get("services") or {}).get(svc)
                down_now = any(d in st.upper() for d in DOWN_STATES)
                first_sight_broken = was is None and down_now
                transition = was is not None and was != st
                if first_sight_broken or transition:
                    kind = ("service_down" if down_now else "service_recovered")
                    events.append({"kind": kind, "machine": mid,
                                   "summary": f"{svc} on {mid}: "
                                              f"{was or UNKNOWN} -> {st}",
                                   "attrs": {"box": mid, "service": svc,
                                             "services": [svc]}})
        return events

    def snapshot_state(self) -> dict:
        return {mid: self.machine_health(mid) for mid in self.machines}
