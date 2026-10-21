# REUSE-VS-BUILD — DIRECTOR-RECON-01

**Principle:** the director is a *consumer* of derived truth + a *mailer* in the
existing protocol — not a new orchestrator. Almost everything it needs exists.

## REUSE — battle-tested, keep as-is

| capability | exact artifact | running? | how the director consumes it |
|---|---|---|---|
| Infra change detection | `scripts/fleet_state.py` (launchd `com.harness.fleetstate`) → `/Volumes/M5_4TB/fleet-state/CHANGES.md` + snapshots | ✅ live | tail CHANGES.md; deltas are already de-noised ("truth is derived, never declared") |
| Meaning/quality change | `scripts/sentinel.py` (hourly/nightly) → `extract-results/sentinel/ALERTS.md`, `last-known-good.json` | ✅ live | ALERTS.md non-empty + drift >2% = P1 signal; canon 5×5 probe = adjudicator health |
| Pipeline event stream | `factory_events` table (immutable, trigger-enforced) in `ra03-state/factory.sqlite3` | ✅ prod | poll `SELECT * FROM factory_events ORDER BY rowid` tail; event vocabulary = discovery/queue/claimed/renewed/expired/completed |
| Agent mail protocol | `/Volumes/M5_4TB/agent-inbox/<box>/` + `shared-docs-queue/outbound/<to>/<ts>_<from>_<to>_<type>_<subject>.md` | ✅ live (13 msgs) | director gets a mailbox `engineering-director/`; sends `status/question/answer/directive` using the SAME naming grammar |
| Agent registry / who's-who | `/Volumes/M5_4TB/AGENT_NETWORK.md` + `BROADCAST.md` | ✅ | static lookup for "message the correct agent"; identify by box, never tool |
| Law boundary | `designwins/docs/DECISIONS.md` (D1–D12, append-only) + `HANDOFF-m5-20261012.md` "Sam's calls, only" | ✅ ratified | the escalation rulebook: anything touching these = human, always |
| Deterministic escalation prep | `scripts/build_datasheet_escalation_queue.py` (mechanical flags only) | ✅ tested | director re-uses the flag vocabulary for its own triage; never invents judgment labels |
| Task/branch tracking | git branch-per-lane + lane-prefixed commits (repo, 2 remotes) | ✅ | `git log --all --since=...` per branch = agent activity feed; unmerged branch age = staleness signal |
| Handoff/knowledge | `HANDOFF-*.md`, `RUNBOOK.md`, `PLAN.md`, `CONTEXT_HANDOFF.md` | ✅ | director's context library; RAG-free: grep is enough at this size |
| Model routing | `config/models.yaml` + `MODEL_SETTINGS.md` law | ✅ (2 keys added tonight) | director calls `asus2_ops_brain` for reasoning, `dgx1_overflow` as fallback; never canon |
| Fast brain | asus2 `:8900` NVFP4 — 24.7 tok/s, qwen3_coder tools 5/5 | ✅ live (tonight) | the director's LLM; P0-only policy + token bucket at client |
| Provenance/release gates | `dw-release` (ReleaseId lineage), promotion packets (`evidence/pin-batch-20261014/PROMOTION_PACKET.md` pattern) | ✅ law + pattern | director NEVER publishes; it watches packet readiness and nags the release owner |
| Facet progress ledgers | `results/mbp-worker-20261014/done-<facet>.txt`, `parked.txt`, `missing-docs.log` | ✅ live tonight | line counts = per-facet velocity; parked growth = poison-PDF signal |
| Queue truth | `results/pipeline-live.db` (`jobs`,`results`,`adjudication_ledger`,`budget_spend`) | ✅ | `budget_spend` = cloud cost signal; dead/claimed ratios = queue health |

## BUILD — genuinely missing

1. **The director loop itself** — nothing today *consumes* CHANGES.md/ALERTS.md/events and decides. fleet_state mails deltas; nobody must answer. **Build:** one always-on observer process on the Ubuntu box (poll → diff → classify → act/defer).
2. **Impact graph (machine-readable)** — the dependency chain (intake → substrate → L0 → facets → verify → canon → fold/board → export/served) exists only in prose (HANDOFF/ARCHITECTURE.md). **Build:** a small YAML/JSON dependency map the director loads: *what breaks downstream if X changes* (e.g., canon settings change → all verdicts suspect → freeze promotion; NAS down → backups+export mirrors degraded, extraction unaffected).
3. **Director state/heartbeat** — no persistent "what has the director seen/decided/escalated" store. **Build:** append-only director journal (same discipline as factory_events: no updates, only appends).
4. **Ubuntu controller onboarding** — the box has no Tailscale identity, no mounts, no keys. **Build:** join tailnet, read-only mirrors, mailbox, systemd units (mirror the `com.harness.*` discipline as `harness-director.*`).
5. **Escalation UI for Sam** — escalations today land as mail files. Acceptable v1; later a digest channel (mail already flows to Sam's boxes via the same protocol).
6. **Cross-machine single-writer discipline** — M5 owns all ledgers today; director must be read-only on them by construction (mount ro / rsync mirrors only). Not a gap in existing infra — a constraint the director must honor, enforced by mounts.

## DO NOT BUILD (explicitly)

- No new orchestrator (Rust `dw-controller` owns queue/lease/recovery — production).
- No competing messaging system (agent-inbox + shared-docs-queue are the protocol).
- No second truth: the director never stores derived facts except in its journal, always with source refs (provenance law, §6 standing rules).
