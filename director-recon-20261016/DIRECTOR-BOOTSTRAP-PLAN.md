# DIRECTOR-BOOTSTRAP-PLAN — DIRECTOR-RECON-01

**target:** dedicated 32GB Ryzen 9955HX Ubuntu controller — permanent DesignWins
Engineering Director. **Doctrine:** observer first, advisor second, actor never
without a granted boundary. No new orchestrator. No competing mail.

## 0. The director's core loop (the "most important" question)

```
DETECT (derived truth only — never trust declarations)
  /Volumes/M5_4TB/fleet-state/CHANGES.md        infra deltas (fleet_state.py, live)
  extract-results/sentinel/ALERTS.md + lkg.json meaning drift (sentinel.py, live)
  factory_events (immutable sqlite tail)        pipeline decisions (prod)
  results/pipeline-live.db budget_spend         cloud spend
  done-<facet>.txt / parked.txt line counts     facet velocity & poison docs
  pdf-intake log cadence                        supply health
  git log --all per lane branch                 agent activity/staleness
        ↓
IMPACT (static dependency map, loaded from YAML — to be authored once)
  intake → substrate → L0 → facet rescans → quote-verify → canon adjudication
         → fold/board → promotion packet → release (ReleaseId) → served export
  Rules encoded, e.g.:
  - canon/verifier change → all verdicts suspect → freeze promotion (P0, human)
  - facet script change → that facet's rescans only (P2, watch parked growth)
  - NAS outage → backups + export mirrors degraded; extraction unaffected (P1)
  - `._` junk wave → queue churn, no data risk (P3, tracked until factory fix)
        ↓
MESSAGE (existing protocol only)
  mailbox: /Volumes/M5_4TB/agent-inbox/engineering-director/
  send: <ts>_engineering-director_<to>_<type>_<subject>.md
        type ∈ {status, question, answer, directive*, work-brief}
        (*directive only after Phase 3 grant)
  route by AGENT_NETWORK.md — identify by box, never by tool
        ↓
ESCALATE (human judgment only — the boundary is already written)
  ALWAYS Sam: DECISIONS.md ratification · cloud spend · freeze changes ·
              alias folds crossing physical quantities
              (HANDOFF-m5-20261012.md "Sam's calls, only")
  P0 auto-page: sentinel drift >2% · canon 5×5 probe failure ·
                promotion-packet integrity · budget_spend anomaly
  Everything else: rules-first (the build_datasheet_escalation_queue.py
  vocabulary: cross_source_disagreement, external_model_failure,
  terminal_local_attempt, package_undercount), model-second, human-last.
```

LLM policy: `asus2_ops_brain` (24.7 tok/s, qwen3_coder tools verified 5/5) for
P0/ambiguous only; thinking-off; token bucket client-side; `dgx1_overflow`
fallback (proven 3.1s failover). Canon is never called by the director.
Deterministic triage handles routine checkins — the model is the exception
path, not the loop.

## 1. Phase 0 — observer (day 1, read-only)

1. **Onboard the box:** Tailscale join (fleet pattern: identify by box →
   `engineering-director`), ssh key per the shared-key law, systemd units
   named `harness-director-*` (mirror the `com.harness.*` discipline).
2. **Read-only mirrors (never mounts of truth):** rsync cron pulls of
   `fleet-state/`, `sentinel/`, facet ledgers, `pipeline-live.db` copy,
   `factory.sqlite3` copy, `shared-docs-queue/` (via git), AGENT_NETWORK.md.
   32GB RAM / local NVMe is ample for mirror + tail state.
3. **Mailbox:** create `agent-inbox/engineering-director/` (NAS) + register a
   row in AGENT_NETWORK.md (append-only edit, announce via BROADCAST.md).
4. **Digest only:** every 10 min the loop writes `director-journal.jsonl`
   (append-only: seen-hash, classification, action=observed). No messages yet.

## 2. Phase 1 — advisory (week 1)

- Detect→impact→message with `status`/`question` types only.
- Daily digest mail to `m5-opencode` + Sam's box; P0 rules auto-mail immediately.
- Alerting thresholds seeded from tonight's baselines: 8.3 tok/s canon,
  24.7 tok/s ops brain, 3/3 verdict parity, facet velocity ≈ docs/hour per box,
  intake 4,980 docs/day, budget_spend slope.

## 3. Phase 2 — escalation authority (week 2, after trust)

- Director drafts escalation packets (evidence-linked, the promotion-packet
  pattern) for everything on the Sam's-calls list and P0 rules; humans still
  pull every trigger.
- Impact map graduates from YAML to checked-in artifact with tests
  (`.harness.toml` unit gate).

## 4. Phase 3 — bounded directives (only on explicit grant)

- Pre-approved directive whitelist, e.g. "pause a facet lane on sentinel P0",
  "re-park a poison wave", "shift overflow traffic". Each directive logged to
  the journal with the rule that fired. Never: publish, spend, amend law,
  touch production DBs write-path.

## 5. What the director must NEVER do

- Never writes to: `pipeline-live.db`, `factory.sqlite3`, facet ledgers,
  NAS export store (§6 provenance law: single-writer per truth).
- Never publishes (dw-release ReleaseId lineage is the release owner's act).
- Never re-litigates DECISIONS.md; only drafts superseding entries for humans.
- Never becomes an extraction orchestrator (dw-controller owns that, live).

## 6. Failure modes to design for (observed tonight)

- dgx1 panics → overflow-only by construction; director treats its loss as P3.
- NAS single-NIC outages → mail spools locally (fleet_state.py SPOOL pattern);
  mirrors go stale — journal records staleness age, never silently trusts.
- Launchd/TCC traps (macOS) — the director on **Ubuntu** sidesteps them; that
  alone justifies the dedicated box.
- `._` junk waves / corrupt timestamps — queue stats must filter known-junk
  patterns or they will cry wolf (176,709 dead letters are noise, not signal).

## 7. Build order (prioritized integration plan)

1. Tailscale + keys + mailbox + AGENT_NETWORK.md registration (½ day)
2. Mirror layer + journal (1 day)
3. Detect loop over the five live feeds (2 days)
4. Impact YAML + P0 rules + escalation drafts (2 days)
5. Ops-brain client w/ token bucket + tool-call schema (1 day, reuse suite)
6. Advisory digests (1 day) → 2-week soak → Phase 2 review with Sam

**Total: ~1.5 weeks to a trustworthy advisory director; zero new truth stores;
every consumed artifact already in production tonight.**
