# SPEC — Association Ledger v1

**Status:** spec (deep) · **Owner:** `dw-knives` schema + spine `identity_bind` · **PRD:** `PRD-ASSOCIATION-LEDGER.md` ·
**Rulings recorded 2026-10-21 (Sam):** R2 family authority = manufacturer family pages, decoder corroborates only; dead-data law (§2 AL5); confidence grounded by multi-model vision consensus (§7.4).

---

## 1. Scope

The join layer between artifacts (any type) and parts (OPN / Family), with
evidence, role, confidence, and epoch. Everything upstream (ripping,
extraction) and downstream (knives, spine, compare) consumes or feeds this
ledger. Not in scope: OPN canonicalization, KIM categories, ranking.

## 2. Laws (frozen; numbered AL to avoid collision with D-laws)

**AL1 — Association is a join, not a property.** Artifacts are immutable
shas with no identity field. All binding lives in the ledger.

**AL2 — Many-to-many is the common case.** Both directions are one-line
lookups via derived indexes.

**AL3 — Role is the authority axis.** Precedence for conflicting claims on
the same (part, parameter): `errata > datasheet > app_note > selection_guide
> marketing`. `ibis_model`, `reference_design`, `image`, `eol_notice` carry
their own semantics (§5.3) and never override parametric claims.

**AL4 — Two-level targets.** `opn | family`. Artifact→OPN may resolve through
a family edge, inheriting that edge's confidence as a **ceiling**.

**AL5 — Dead-data law (ruling 2026-10-21).** *An artifact with no
association ≥ threshold is **inert**: it contributes to no knife, no
comparison, no user-facing surface.* Provenance means we only ever show what
has a source; showing an unsourced datum is a violation, not a gap. Gaps are
counted in telemetry (`no_target_found`, `below_threshold`) and are visible
to engineering, never rendered as content.
  - Distinction preserved: an **UNKNOWN verdict** (sourced, insufficient
    evidence to decide) surfaces as unknown. A **dead artifact/row** (no
    association) never surfaces at all. Unknown ≠ dead.

**AL6 — Evidence or it does not exist.** Every association carries verbatim
evidence with locators. Confidence without retained evidence is 0. No
method may delete; supersede only (epochs).

**AL7 — Confidence annotates, never eliminates.** Association confidence
appears in knife provenance chains. It never turns a sourced UNKNOWN into a
FAIL or eliminates a part from a comparison.

**AL8 — No self-attestation above 0.50.** A document quoting itself is one
evidence kind. Two independent kinds (URL + quote, or quote + second
document citing it) are required to exceed 0.50.

## 3. Entities

| entity | identity | notes |
|---|---|---|
| Artifact | sha256 | typed: `pdf html image model bom text` |
| OPN | literal string | canonicalization out of scope (§1) |
| Family | `(manufacturer, family_key)` | family_key from manufacturer page slug |
| Family edge | OPN→Family | authority: mfg family page (§4) |
| Association | `(sha, target, role, method)` keyed | §5 |

## 4. Family-edge authority model (R2 — decided 2026-10-21)

**Authority: manufacturer family pages.** A family edge exists iff a
manufacturer-published page (family landing page, selection-table page,
ordering-info table in a family datasheet) lists the OPN under the family.

**Corroboration only: OPN decoder.** Decoder output never creates an edge;
it can only (a) raise a corroborated edge's confidence, (b) flag a
*disagreement* for review.

### 4.1 Family-edge record

```jsonl
{"schema": "harness.assoc.family_edge.v1",
 "manufacturer": "STMicroelectronics",
 "family_key": "stm32f1",
 "opn": "STM32F103C8T7",
 "authority": {"kind": "mfg_page", "url": "https://.../stm32f1-series.html",
                "quote": "STM32F103C8T7", "captured_sha": "<page snapshot sha>",
                "captured_at": "..."},
 "corroboration": [{"kind": "opn_decoder", "decoded_family": "STM32F1"}],
 "status": "active | superseded | disputed",
 "confidence": 0.95, "epoch": 2}
```

- Page snapshot is content-addressed (captured_sha) — the authority itself
  has provenance.
- Decoder disagreement sets `disputed`: edge stays usable at reduced
  confidence (−0.10) and enters the review queue; never silently flipped.
- Family-page crawls are re-runnable; new epoch supersedes.

### 4.2 Launch source

Wave 1: family pages already present as corpus artifacts (selection guides,
ordering tables — the rip `series_summary`/`opn_decoder` lanes locate them).
Wave 2: targeted crawl of manufacturer family landing pages for the top
manufacturers by part count. Decoder corroborates throughout.

## 5. Association records

### 5.1 Schema

```jsonl
{"schema": "harness.assoc.ledger.v1",
 "artifact_sha": "...", "artifact_type": "pdf",
 "target_kind": "opn", "target": "STM32F103C8T7", "manufacturer": "STMicroelectronics",
 "role": "datasheet",
 "method": "manifest_url | page1_opn | family_graph | citation | mfg_page | vision_consensus",
 "confidence": 0.98,
 "evidence": [ {"kind": "url_pattern|quote|family_edge|citation", ...} ],
 "epoch": 3, "discovered_at": "...", "discovered_by": "..."}
```

### 5.2 Roles

`datasheet, app_note, errata, selection_guide, eol_notice, ibis_model,
reference_design, image, marketing, unknown`
- `errata`: overrides per AL3; must cite the affected datasheet claim.
- `eol_notice`: lifecycle authority (NRND/EOL dates) — the only role that
  may set lifecycle state.
- `selection_guide`: may seed parametrics at reduced confidence (0.8×
  method ceiling); never authoritative over datasheet.

### 5.3 Concurrent-writer merge discipline (spec'd — was open in PRD)

- Writers: `l0_ingest` (methods 1–3, seconds after delivery) and
  `assoc_agent` (methods 4–6, batched). Both append-only to sharded JSONL by
  `sha[:2]`.
- Key `(sha, target_kind, target, role, method)`; on collision the record
  with higher `(epoch, confidence)` wins reads; both retained.
- Epoch = method-generation number, owned centrally per method (bumping a
  method's implementation bumps its epoch) — not a timestamp, so old-method
  records lose to new-method records deterministically.

## 6. Methods ladder (frozen ceilings)

| # | method | ceiling | creates | notes |
|---|---|---|---|---|
| 1 | `manifest_url` | 0.98 | opn + family | distributor/factory manifest row |
| 2 | `page1_opn` | 0.90 | opn | current stamp method, quote retained |
| 3 | `opn_decoder` | 0.80 (corroboration only) | — | never creates; raises/flags §4 |
| 4 | `family_graph` | inherits family-edge ceiling | opn via family | artifact→family edge + family→OPN edge; ceiling = min(both) |
| 5 | `citation` | 0.70 | opn + family | cross-doc refs: "replaces X", app-note headers, see-also |
| 6 | `vision_consensus` | 0.50–0.90 | opn + family | §7.4 |

## 7. Confidence algebra

### 7.1 Base
`confidence = min(method_ceiling, 1 - Π(1 - e_i))` over retained independent
evidence kinds `e_i` (each evidence kind contributes its own reliability:
url_pattern 0.95, quote 0.85, family_edge 0.90, citation 0.70).

### 7.2 Multi-method consensus
Two methods agreeing on the same `(sha, target, role)`: combined =
`min(0.99, 1 - (1-c1)(1-c2))`. Three or more: cap 0.99.

### 7.3 Disagreement
Two methods proposing **different** targets for the same role: both records
written; both marked `conflict`; excluded from spine/extract reads;
review queue (engineering telemetry). Resolution: higher-authority evidence
(manifest or mfg page) or human ruling; never silent merge.

### 7.4 Vision consensus (ruling 2026-10-21: "hit it 200 ways")
For `vision_consensus` proposals, the same page is submitted to **three
independent engines**: MiMo cloud, QwenCloud DeepSeek, local TP4.
- unanimous 3/3 on OPN + verbatim quote on page → 0.90
- majority 2/3 → 0.70 (capped below AL8's two-kind rule unless URL/citation
  corroborates)
- split 3-way → `conflict`, quarantine
Cost: ~$0 (subscriptions), ~40s wall at 3-way parallel — per-doc, only for
the never-stamped 12.5K docs and conflict review. This is the ground-truth
hammer: no single model's claim stands alone.

### 7.5 Thresholds (calibration gate required before Phase 2)
Working values: extract ≥0.70, spine/compare ≥0.80. **Must be calibrated**
on a 500-doc gold set (hand-ruled associations) measuring precision/recall
per threshold before freezing; calibration report appended to this spec.

## 8. Dead-data semantics (AL5 operational)

| consumer | rule |
|---|---|
| extraction lanes | consume only `(artifact, assoc ≥0.70)` |
| spine / compare | only ≥0.80 |
| frontend / any surface | only what the spine serves; no path exists from an unassociated artifact to any surface |
| telemetry | counts `no_target_found`, `below_threshold`, `conflict` per lane — engineering-visible only |

## 9. Rip adapters (type → structure)

| type | adapter | output shape |
|---|---|---|
| pdf | existing rip lanes | per-page lane tags (live) |
| html | DOM lanes: headings, tables, links, meta | same page/lane shape, `page=1` for single-page captures |
| image | OCR lanes (qwen vision) | page=1, quote-evidence from OCR |
| model (IBIS etc.) | header parser | key/value header lanes |
| bom | table parser | line-item lanes |
| text | trivial | page=1 |

All adapters emit the same downstream shape; methods 1–6 are adapter-blind.

## 10. Pipeline (L0 watcher integration, live today)

```
delivery → sha256 + type tag → artifact INDEX
         → rip (adapter) → page/lane index
         → assoc methods 1–3 (deterministic, ingest-time)
         → assoc methods 4–6 (batched: family crawl, citation, vision_consensus)
         → extraction lanes (vision subscriptions, ~13k docs/h measured)
         → spine: rows join parts THROUGH ledger only
```

## 11. Storage & compaction

- Append-only JSONL sharded `sha[:2]` on NAS SMB-writable path (vault-ingest
  sibling: `assoc-ledger/`), mirrored to m5 local for reads.
- Derived indexes (`by_opn`, `by_sha`, `by_family`) rebuilt by a compaction
  job; compaction = rewrite per-shard keeping max-epoch per key + conflict
  set, emitting stats. Ledger never rewritten outside compaction.

## 12. Component mapping

| piece | where |
|---|---|
| schema + laws + thresholds | `dw-knives` (`assoc` module, Rust) |
| backfill (stamps/registry/manifests → ledger) | Python `assoc_backfill.py` |
| methods 1–3 at ingest | L0 watcher hook (Python) |
| family crawler + edge builder | Python `family_edges.py` (wave 1: corpus artifacts; wave 2: mfg pages) |
| methods 5–6 agents | Python, vision subscriptions |
| spine join | `identity_bind` stage reads ledger (dual-read per PRD §8) |
| errata precedence | `dw-decide` (Phase 3) |

## 13. Gates

| gate | criterion |
|---|---|
| G1 rebind | pins ≥85%, curves ≥85% bound (was 65.9/66.1) |
| G2 coverage | ≥95% of corpus docs have ≥1 assoc ≥0.70 **or** explicit `no_target_found` with search evidence |
| G3 errata override | 20-doc gold set: 100% show errata value with `supersedes` provenance |
| G4 no-regression | dual-read staging bit-identical (16,014 parts / 31.6M pairs / 53,518 rows) |
| G5 calibration | 500-doc gold precision/recall report at thresholds 0.70/0.80; frozen values appended |
| G6 dead-data audit | probe: zero unsourced rows reachable from any surface; telemetry counts match ledger |

## 14. Phasing

0. Schema + backfill + G4 dual-read (days)
1. Methods 1–4 + family wave 1 + G1 attempt
2. Calibration G5 → freeze thresholds → Phase 2 read-flip (ledger wins)
3. Methods 5–6 (vision_consensus on never-stamped 12.5K) + G2
4. Roles live, errata precedence in `dw-decide` + G3 + G6 audit

## 15. Open items

- pmsig registry richer fields (ground_truth, published_pinouts) mapping —
  hold until Phase 1 backfill design.
- Family-page crawl politeness/robots policy for wave 2.
- `assoc-ledger/` NAS write rights — same chmod decision as vault-ingest.
