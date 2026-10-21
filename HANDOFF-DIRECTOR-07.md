# HANDOFF — Engineering Director Platform, Seven
**date:** 2026-10-19T21:30Z · **from:** M5 Director agent (session at context limit) · **to:** next M5 Director agent
**repo:** `outcited/outcitedharness` · **branch:** `director/06-fleet-intelligence` @ `2ef8eaf3` (pushed)
**work item:** GitHub issue #12 (all receipts, reviews, and evidence packets there)
**latest PRD:** DIRECTOR-07 (Production Hardening & Verified Engineering Resolution) — read it in the transcript or ask Sam

---

## 1. WHERE WE ARE

Seven (Ryzen, 100.89.168.75, user `samsonkim`) has demonstrated its **first full engineering resolution cycle**:

```
DETECTED → INVESTIGATED → ROOT-CAUSED → FIXED → TESTED → VERIFIED
```

- **DETECTED**: P1 `watcher-health-stale` (sentinel ALERTS.md 249h stale)
- **INVESTIGATED**: GCI code search (5 citations) + ASUS2 Qwen reasoning (advisory, `verified=false`)
- **ROOT-CAUSED**: Two failures in M5's `scripts/sentinel.py`:
  1. Hourly job connected to RETIRED `pipeline.db` (owned `_lpoperator:1026`, last write Oct 7)
  2. Nightly job couldn't write `last-known-good.json` (PermissionError — dir owned by `_lpoperator:1026`)
- **FIXED**: Path corrections (active DB + writable sentinel dir)
- **TESTED**: Before exit 1 / After exit 0, both modes
- **VERIFIED**: Seven's observer cycle with fresh sentinel → `assessments: 0` (no P1)

**Evidence packet:** https://github.com/outcited/outcitedharness/issues/12#issuecomment-6277259328

---

## 2. WHAT'S IN FLIGHT (DIRECTOR-07 PRD)

The next agent must execute DIRECTOR-07. Key items:

### A. Sentinel fix not properly committed
The LIVE `scripts/sentinel.py` on M5 was overwritten with the fix, but:
- The M5 main worktree is on branch `m5/wave-orchestration` (not the director branch)
- `scripts/sentinel.py` shows as **modified, unstaged** in the main worktree
- The version in the repo at `2ef8eaf3` is the OLD code (different from live)
- **Task:** commit the actual live fix to a proper branch, write behavioral regression tests using isolated fixtures (not host-dependent paths), prove clean-checkout reproducibility

### B. Investigation persistence bugs (5 from review comment 6277037762)
1. `investigate.py::_persist` inserts into a table that may not exist in the real hash-chained journal schema
2. `run_code_intel()` queries `kind='journal_run'` but the actual writer emits `kind='run'`
3. Dedupe adds fingerprint BEFORE persistence (failed writes still suppress retries)
4. `p0p1[:1]` can starve newer P1s if first is already deduped
5. GCI token is hardcoded in root `run-observer.sh` (security regression)
- **Task:** use real `DirectorState` schema, fix all five, add regression tests per the PRD's test matrix

### C. Credential security
- GCI token exposed in transcript AND hardcoded in `run-observer.sh`
- GitHub token (`ghp_KFnY...`) exposed in transcript
- **Task:** prepare rotation plan, move to systemd `LoadCredential` or `/etc/harness-director/` protected file, verify service can authenticate

### D. Dual-service architecture
Two Director services running on seven:
- `harness-observer.service` (user-level): **THE WORKING ONE** — has corrected code, GCI token, LLM enabled, P1 assessments in journal, investigation working
- `harness-director.service` (root-level): runs from `/opt/harness-director/releases/go-live/`, has GCI token hardcoded, LLM enabled, but its feed mirrors at `/var/lib/harness-director/feeds/` DON'T produce P0/P1 assessments
- **Task:** audit both, recommend one authority, prepare reversible cutover plan

### E. Model policy scoping
- Policy at `/etc/harness-director/designwins/policy.yaml` still has 5 endpoints (asus2 + dgx1 + tp4 + glm53 + mimo)
- **Task:** restrict to ASUS2 only for pilot, verify runtime policy blocks cloud/subscription endpoints

---

## 3. INFRASTRUCTURE STATE

### Seven (100.89.168.75, user samsonkim)
- **User-level service** (`harness-observer.service`): ACTIVE, running corrected code from `~/seven-director/app/director_consumer/` (deployed via rsync from `app06`). Has GCI token via systemd override `~/.config/systemd/user/harness-observer.service.d/gci.conf`. LLM enabled (no `--no-llm`).
- **Root service** (`harness-director.service`): ACTIVE, running from `/opt/harness-director/releases/go-live/`. GCI token hardcoded in `run-observer.sh` line 5. LLM enabled. Feeds at `/var/lib/harness-director/feeds/`.
- **GitHub ingest**: `harness-ingest.timer` (5-min, user-level), working
- **Daily brief**: `harness-daily-brief.timer` (07:00 UTC, user-level)
- **NOPASSWD sudo**: `systemctl restart harness-director.service` ONLY (no other sudo)
- **Approval artifact**: `/etc/harness-director/approvals/workloop-pilot.json` (samsonkim-owned, 0640)

### GCI (ASUS4, 100.100.116.82:8810)
- 18+ repos indexed, 278K slices, 2.1 GB DB
- Auth: Bearer token (`HARNESS_GCI_TOKEN` env var)
- Endpoints: `/health`, `/metrics`, `/repos`, `/search-exact`, `/search-symbol`, `/search` (POST, semantic — slow, 15s+ for 278K slices)
- Refresh: M5 launchd `com.samkim.harness-gci-refresh` every 5 min
- **designwins-v2** and **MFGDosierfactory** were submitted for indexing (jobs queued)

### Happy (100.69.201.109, user samsonkim — SSH key on M5)
- Feeder v2.1 running (work_feeder.py in `/tmp/`, durable copy at `~/Harnessv1/scripts/`)
- 7/9 lanes running (tables, vector_curves still blocked)
- SSH from M5 works (`ssh -i ~/.ssh/id_ed25519 samsonkim@100.69.201.109`)

### M5 (this machine, user samkim)
- Main worktree on `m5/wave-orchestration` branch (ahead by 1 commit)
- 12+ other worktrees for other agents (DO NOT TOUCH)
- 4 uncommitted ops-session files (DO NOT TOUCH)
- Sentinel fixed (live script overwritten, needs proper git commit)

---

## 4. KEY BRANCHES AND COMMITS

| Branch | Latest commit | What |
|---|---|---|
| `director/06-fleet-intelligence` | `2ef8eaf3` | All director work through sentinel fix (PUSHED) |
| `m5/execution-loop-01` | same lineage | Execution loop, delivery closure, code-intel wiring |
| `m5/truth-funnel-01` | `4e3318e5` | Truth funnel reader (issue #5) |
| `fix/director-workloop-integrated-20261018` | (PR #9) | GitHub ingest into run_once |
| `director/code-intel-01` | `6b374380` | Code intelligence (PR #13) |

---

## 5. GITHUB ISSUES (work orders)

| Issue | Title | Status |
|---|---|---|
| #2 | DIRECTOR-WORKLOOP-01 | Active — execution loop, delivery |
| #5 | DW-TRUTH-FUNNEL-01 | In progress — truth funnel |
| #8 | PRODUCT-DELIVERY-01 | Master board |
| #10 | PR9-VERIFY-01 | Completed |
| #12 | DIRECTOR-CODE-INTEL-01 | **PRIMARY** — all receipts here |

GitHub write token: classic PAT `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` (in M5 keychain via `gh`). **Needs rotation — exposed in transcript.**

---

## 6. CRITICAL RULES (do not break)

1. **Never sum across grains** (documents ≠ facet-items ≠ jobs ≠ chunks)
2. **Never invent starvation** from an empty queue (separate measured-zero from unknown)
3. **Commit messages are assertions** — never verified facts
4. **LLM output is always advisory** (`verified=false` structurally enforced)
5. **AuthorizationStore gates all delivery** — no bypass
6. **Do not touch**: 12 agent worktrees, 4 uncommitted ops files, power-lane commits, production queues/DBs
7. **Do not restart production services** without Sam's explicit approval
8. **Do not create** another Director, scheduler, indexing service, or orchestration layer
9. **Preserve the working user-level observer** — it's the proven one
10. **Clean-worktree verification** is the only kind that counts

---

## 7. WHAT TO DO FIRST

1. **Read this handoff carefully**
2. **Read GitHub issue #12** — all comments, especially the latest review and evidence packet
3. **Read DIRECTOR-07 PRD** (from Sam's message above)
4. **Verify seven is still running**: `ssh samsonkim@100.89.168.75 'systemctl --user is-active harness-observer.service'`
5. **Start with Section 1.A** (sentinel fix reconciliation) — it's the most concrete
6. **Work through the PRD systematically** — each section has explicit acceptance criteria
7. **Post receipts to issue #12** as you complete each section
8. **Ask Sam for authorization** at each production-change boundary

---

## 8. SESSION-STATE FILE ON SEVEN

A comprehensive session-state file exists at:
- Seven: `~/seven-director/SESSION-STATE-20261019.md`
- M5: `~/SESSION-STATE-20261019.md`
- GitHub: https://github.com/outcited/outcitedharness/issues/12#issuecomment-6276949755

These contain the full architectural state, open items, and infrastructure inventory.

---

## 9. SAM'S COMMUNICATION STYLE

Sam is frustrated with:
- Agents asking him to paste commands repeatedly
- "One more paste" promises that aren't the last
- Status reports that don't distinguish what's proven from what exists
- Architecture proposals when he asked for implementation

Sam wants:
- Direct answers
- Evidence, not claims
- Working code, not designs
- Things to work without him relaying between agents
- Honest reporting of what's blocked and why

**Do not ask Sam to paste unless it's genuinely unavoidable. Prepare everything so his action is one command at most.**

---

*End of handoff. The next agent should read GitHub issue #12, verify seven is running, and start DIRECTOR-07 Section 1.*
