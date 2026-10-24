# PRD — the Association Ledger: every artifact, bound to OPN and Family

**Status:** for review · **Owner:** `dw-knives` (ledger schema) + extraction spine (`identity_bind` stage) · **Gates:** rebind gate (§9.1), errata-override gate (§9.3), dual-read no-regression (§9.4) · **Companion:** `DECISIONS.md` D5 (unmapped-phrase policy — same never-invent principle), `PRD-PHRASE.md` §1 (why axes starve without substrate)

---

## 1. Problem

### 1.1 Measured: a third of verified extraction cannot reach a part

The vision lanes finished clean. Then identity binding ran, and the numbers:

| lane | verified rows | bound to a part | lost |
|---|---:|---:|---:|
| pin tables (2026-10-21) | 38,691 | 25,494 (**65.9%**) | **13,197** |
| typical curves (2026-10-21) | 42,963 | 28,381 (**66.1%**) | **14,582** |

28K verified, quote-provenanced rows sit unusable. They are unusable for the
same reason in both lanes: **the document they came from has no part identity.**

### 1.2 Why: identity is a stamp, not a join

Current identity pipeline (`identity_stamp.py`): one shot, per document, two
methods, keep the winner:

```
stamps over 46,856 docs (of 59,369 corpus — 78.9% ever stamped)
  manifest_url          32,251   (68.8% of stamped)
  page_1_title_line     10,128   (21.6%)
  unresolved             4,203
  mfr_only / no_mfr      2,672
```

Three structural failures, in order of cost:

1. **12,513 docs (21% of corpus) were never stamped at all.** They are
   disproportionately the artifacts engineers actually cross-read: app notes,
   family selection guides, errata, IBIS/model files. The stamp pipeline
   targeted datasheets because datasheets carry OPNs on page 1. App notes
   don't — and app notes often contain the *only* characterization data a part
   has.
2. **Association is binary and unversioned.** A doc is stamped to one OPN or
   unresolved. A family datasheet covering 40 OPNs gets one stamp at best;
   the 39 other parts never see it. There is no record of *evidence*, no
   confidence, no method, no time — so nothing can be re-derived when a
   better method ships.
3. **No role, no override.** Errata and datasheets are indistinguishable to
   the spine. An errata that retracts a spec cannot supersede the datasheet
   claim it retracts, because "what this artifact *is* for the part" is not
   recorded anywhere.

### 1.3 The platform constraint (stated by Sam, 2026-10-21)

The corpus is becoming multi-type. The PDF factory will deliver app notes,
selection guides, errata, EOL notices, model files, images, HTML captures.
Without artifact→OPN/Family association, **the knives cannot manifest** —
a knife question ("does this part satisfy X?") must first know which
documents speak for the part, in what role, with what authority.

---

## 2. Goals

1. Every artifact in the corpus — PDF or not — can carry **zero or more**
   evidential associations to OPNs and Families, each with method, evidence,
   confidence, epoch.
2. Recover the lost third: pin/curve row binding from 66% → **≥85%** (§9.1).
3. Make `role` first-class so errata can override datasheets (§9.3).
4. One ledger absorbs the three current identity stores (corpus stamps, pmsig
   registry, delivery manifests) — additive, dual-read, no spine regression.
5. Type-agnostic ingestion: the L0 watcher contract extends to any artifact
   type with a structural-rip adapter.

## 3. Non-goals

- **OPN canonicalization.** Deciding that `STM32F103C8T7` and
  `STM32F103C8T7TR` are the same commercial entity is the part-registry
  problem — adjacent, depended on, not built here. The ledger stores the OPN
  string and its evidence; a canonicalizer consumes it later.
- **KIM category changes.** Family→category binding already exists; the
  ledger stops at Family.
- **Replacing stamps today.** Dual-read migration only (§8). Stamps remain
  authoritative until the rebind gate passes.
- **Inventing associations.** No method may create an association without
  evidence retained verbatim (same law as quote verification).

---

## 4. Semantics — frozen

**S1 — Association is a join, not a property.** An artifact has no identity
field. Association records live in a ledger keyed `(artifact_sha, target,
method)`; artifacts are immutable shas forever.

**S2 — Many-to-many is the common case, not the exception.** One artifact →
many OPNs (family guide). One OPN → many artifacts (datasheet + errata +
app note). The ledger must make both directions a one-line lookup.

**S3 — Role is the authority axis.**
`datasheet | app_note | errata | selection_guide | eol_notice | ibis_model |
reference_design | image | marketing | unknown`. Override precedence for
claims about the same part+parameter: `errata > datasheet > app_note >
selection_guide > marketing`. Errata beats everything; marketing beats
nothing.

**S4 — Two-level targets, because difficulty is asymmetric.**
`target = OPN | Family`. Artifact→Family is easy (family docs self-declare);
OPN→Family comes from decoders/parametrics (rip `opn_decoder` lane already
exists). Artifact→OPN resolves *through* the family when direct evidence is
missing, and inherits the family edge's confidence as a ceiling.

**S5 — Every association carries its evidence.** `evidence[]` holds verbatim
quotes with (sha, page) locators and/or URL patterns. An association that
cannot show its evidence does not exist. Confidence without evidence is
`0`.

**S6 — Associations are never deleted, only superseded.** Ledger is
append-only with epochs. A better method adds a higher-epoch record; the
spine reads max-epoch per `(sha, target, role)`.

**S7 — Association confidence propagates into knife provenance.** A knife
row derived via a 0.7-confidence association shows that in its provenance
chain. Unknown never eliminates (existing law); low-confidence association
likewise never eliminates — it annotates.

---

## 5. Data model

```jsonl
{"schema": "harness.assoc.ledger.v1",
 "artifact_sha": "<sha256>",
 "artifact_type": "pdf | html | image | model | bom | text",
 "target_kind": "opn | family",
 "target": "STM32F103C8T7",
 "manufacturer": "STMicroelectronics",
 "role": "datasheet",
 "method": "manifest_url | page1_opn | family_graph | citation | opn_decoder | llm_verified",
 "confidence": 0.98,
 "evidence": [
   {"kind": "url_pattern", "url": "https://.../stm32f103c8.pdf", "matched": "stm32f103c8"},
   {"kind": "quote", "sha": "<same>", "page": 1, "quote": "STM32F103C8T7"}
 ],
 "epoch": 3,
 "discovered_at": "2026-10-21T17:00:00Z",
 "discovered_by": "l0_ingest | assoc_agent_v2 | family_walker"}
```

Indexes (derived, rebuildable): `by_opn(opn) → [sha, role, conf]`;
`by_sha(sha) → [(target, role, conf)]`; `by_family(family) → [opn…]`.

---

## 6. Methods ladder

Deterministic first; LLM proposes, evidence disposes. Confidence ceilings are
frozen per method:

| # | method | ceiling | input | notes |
|---|---|---|---|---|
| 1 | `manifest_url` | 0.98 | delivery manifest row | distributor URL/row carries OPN or family id |
| 2 | `page1_opn` | 0.90 | page-1 text | current stamp method, unchanged |
| 3 | `opn_decoder` | 0.80 | OPN string structure | family from part-number decode; page evidence where present |
| 4 | `family_graph` | 0.75 | family→OPN edges + artifact→family | inherits family edge confidence as ceiling (S4) |
| 5 | `citation` | 0.70 | cross-doc references | "replaces OPN-X", "see AN-1234", app-note headers naming parts |
| 6 | `llm_verified` | 0.50–0.90 | vision/text proposal + verbatim quote on page | same verification law as pin lanes; quote must appear on claimed page |

**Hard rule (circularity guard):** a document never attests *itself* into a
higher method than its evidence class. An `llm_verified` claim quoting the
doc's own marketing line is still capped at 0.50 unless a second evidence
kind (URL, or a *different* document citing it) corroborates. Single-source
self-attestation never exceeds 0.50.

---

## 7. Ingestion contract (extends the live L0 watcher)

1. Factory delivers `{file, manifest row(url, brand, doc_type, opn_or_family?)}`.
2. L0 watcher: sha256 → artifact store (type-tagged) → artifact INDEX.
3. Structural rip per type: PDF lanes (existing), HTML DOM lanes, image OCR
   lanes — same page/lane index shape.
4. Association agents run ladder methods 1–3 at ingest (seconds, deterministic);
   methods 4–6 batch via the vision subscriptions.
5. Extraction lanes consume `(artifact, association ≥ threshold)` pairs — an
   artifact feeds a part's knife **only through** a ledger entry.
6. Provenance chain end-to-end: knife row → extraction row → (sha, page) →
   association evidence.

Threshold policy: extraction ≥0.70; spine/compare ≥0.80; anything below is
quarantined, visible, re-runnable — never silently dropped (S7).

## 8. Migration — dual-read, stamps stay authoritative

- **Phase 0 (now):** ledger schema in `dw-knives`; backfill = import stamps
  (method per source field) + pmsig registry + delivery manifests. Read path
  unchanged.
- **Phase 1:** spine reads `stamps ⨝ ledger` (union, stamps win conflicts).
  Gate: §9.4 no-regression on staging numbers.
- **Phase 2:** ledger wins conflicts; stamps become read-only legacy. Gate:
  §9.1 rebind ≥85%.
- **Phase 3:** roles live; errata precedence in `dw-decide`. Gate: §9.3.

Rollback at every phase: delete ledger reads, stamps still answer.

## 9. Acceptance criteria

**9.1 Rebind gate.** Pin rows bound ≥85% (was 65.9%); curve rows ≥85%
(was 66.1%). Measured by the same binder, ledger on vs off.

**9.2 Coverage gate.** ≥95% of corpus docs have ≥1 association (any target,
any method, incl. `family` at 0.75) or are explicitly ledger-marked
`no_target_found` with evidence of the search.

**9.3 Errata-override gate.** A gold set of 20 known errata-retracted specs:
spine shows the errata value with `supersedes` provenance, not the datasheet
value, in 100% of cases.

**9.4 No-regression gate.** Phase-1 dual-read reproduces staging exactly:
16,014 parts, 31.6M pairs, 53,518 compare rows — bit-identical or better.

## 10. Risks

| risk | severity | mitigation |
|---|---|---|
| **Over-association poisoning**: one bad family edge binds a guide to 100 wrong OPNs | high | family edges require their own evidence; quarantine semantics (cr-core pattern); poison audit in §9 gold |
| **Ledger write amplification**: 59K docs × k associations | medium | append-only JSONL + epoch compaction; indexes are derived |
| **Decoder hallucination** (OPN→Family wrong) | high | decoder capped 0.80, gold-gated like normalization; family edges re-derivable |
| **Circular self-attestation** (LLM quotes doc's own claims) | high | §6 hard rule: single-source self-attestation caps at 0.50 |
| **Stamp/ledger divergence during dual-read** | medium | stamps win until Phase 2; conflicts logged, counted, gated |
| **Non-PDF rip adapters late** | low | PDF path ships first; adapters additive, no schema change |

## 11. Sequencing

1. Schema + backfill (Phase 0) — days, no read-path risk.
2. Deterministic methods 1–4 live at ingest — with the L0 watcher.
3. Rebind gate (§9.1) on pins/curves — the lost 28K rows.
4. Method 5–6 (citation + LLM) via subscriptions — the never-stamped 12.5K docs.
5. Roles + errata override (Phase 3) — `dw-decide` change, gold set first.

## 12. Review — open questions, resolved by this review

**R1 — Is the ledger over-engineered vs better stamps?** No, but only because
of §1.2 failures 2–3: many-to-many and roles are not expressible in a stamp.
If the corpus were single-type datasheets only, stamps + a URL join would
suffice. It isn't.

**R2 — Family graph authority.** **RESOLVED 2026-10-21 (Sam): manufacturer family pages are the authority; OPN decoder is corroboration only** (it can raise confidence or flag disagreement; never create an edge). Full model in `SPEC-ASSOCIATION-LEDGER.md` §4. Dead-data law and vision-consensus grounding also ruled — spec §2 AL5, §7.4.

**R3 — Does association confidence leak into verdicts improperly?** Guarded
by S7: annotation, never elimination. But the *display* semantics on the
frontend (seven's lane) are hers to design — flagged to her feed.

**R4 — What if a knife needs an artifact nobody associated?** The
`no_target_found` explicit marker (§9.2) keeps the gap visible and measurable
rather than silently absent — same philosophy as D5 unmapped-phrase policy.

**R5 — Non-PDF types before adapters exist?** They ingest, get method-1
associations from manifests, and sit unripped. Ledger-first, rip-later is a
valid state; the reverse (ripped but unassociable) is not.
