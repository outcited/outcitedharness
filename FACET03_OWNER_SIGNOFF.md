# FACET03 OWNER SIGNOFF — independent admission review package

Prepared per owner-review directive. **Stop-before-admission honored:** no
`promote()` called, no catalog altered, no unresolved provenance or
quote-verification state changed, packets remain `proposed`.

## 1. Packet integrity (immutable hashes)

| Packet | SHA-256 | Members |
|---|---|---|
| family-optimos-review-v1.json | `7a65e8805e7c7602cf63aefc38869bdf5c811c478adce320b3439748ee194e54` | 243 |
| family-coolmos-review-v1.json | `071ec975ee0267c231bbd95bd5d8f6538f271c63f249f56aca60d0d8b6cf5840` | 207 |
| family-coolsic-review-v1.json | `b2e4a60b961dd3817ad967544ca4fa0e279d4bca739ee4fdf49c3cba35323b7b` | 127 |
| family-strongirfet-review-v1.json | `701c45b653ee91c80d29107164267e29ad92e1348beb79fb3b4374921adabe38` | 49 |

Population check: packets total **626 members == identity-db relationship
count (626)** == sum of per-family counts. No drift between packet and
store.

## 2. Sampling (reproducible, risk-stratified, deterministic)

77 members sampled (12.3% of population). Strata per family:
**A** truncation-affected records (recomputed from the pre-fix capture
rule, not remembered), **B** duplicate-document members (one source sha
serving multiple OPNs), **C** series-ambiguity (family token printed with
a series designator v1 does not capture), **D** ordinary representatives
(fixed stride over sorted ids). Sampled IDs are listed in full in
`owner-review-results-pass2.json` (`families.*.strata`), including all
five recomputed StrongIRFET A-stratum records: IRF100B201, IRFP7430,
IRFP7530, IRFP7537, IRL40S212.

## 3. Independent verification method

For each sampled member, from source (not from the identity db's cached
quote): locate the manufacturer PDF → **re-hash the actual bytes against
the recorded SHA-256** → open with PyMuPDF → confirm the family token on
the recorded page → confirm the OPN is named in the same front matter →
confirm the token and OPN co-occur (attribution, not mere presence) →
comparative-mention check on the 40 chars PRECEDING the token (a family
name appearing in a document does not by itself prove membership).

## 4. Results per family

| Family | Sampled | Supported | Ambiguous | Unsupported | Evidence errors |
|---|---|---|---|---|---|
| OptiMOS | 15 | 15 | 0 | 0 | 0 |
| CoolMOS | 21 | 21 | 0 | 0 | 0 |
| CoolSiC | 15 | 15 | 0 | 0 | 0 |
| StrongIRFET | 26 | 25→**26** (pass 2) | 1→0 | 0 | 0 |
| **Total** | **77** | **77** | **0** | **0** | **0** |

Two disclosed passes (both retained on disk):
- **Pass 1** (`owner-review-results-pass1.json`): 76 supported, 1
  ambiguous (IRF100B201). Root cause was a review-harness rule defect,
  NOT a membership defect: the comparative marker regex scanned the whole
  ±context window and fired on a figure title ("Typical On-Resistance
  **vs.** Gate Voltage"). Manual inspection: the document title line reads
  "StrongIRFET IRF100B201/IRF100S201" — direct attribution.
- **Rule correction** (common rule identified per directive item 6):
  comparative markers now count only when they precede the family token
  within 40 chars. **Pass 2** (`owner-review-results-pass2.json`): 77/77
  supported. No packet was held — the defect was in the harness, the
  membership evidence was sound; both passes are preserved for audit.

## 5. Notable observations (non-blocking)

1. **Dual-OPN documents**: IRF100B201 shares one datasheet with IRF100S201
   (title names both). The B-stratum (6 CoolMOS + 6 StrongIRFET members on
   shared shas) verified clean; membership from a shared document is
   legitimate because the title attributes the family to both OPNs, but
   owners should know the evidence is document-shared.
2. **Series designators are printed but not captured** (CoolMOS CFD2/P7/CE,
   CoolSiC M2H/G1, OptiMOS LS5/NS5, StrongIRFET F2S): C-stratum members
   (32 sampled) are family-correct; series-grain identity remains a v2
   extension. The review's series re-detection reported 0 because the
   PDF-extracted context renders ™/ª differently than the recorded quote —
   cosmetic, verdicts unaffected.
3. Quote-verification states from the admission review (404 normalization
   gap / 58 absent / 120 unverifiable / 5 unresolved stems) were NOT
   touched and remain as recorded.

## 6. Per-family admission recommendations

| Family | Recommendation | Basis |
|---|---|---|
| OptiMOS (243) | **RECOMMEND APPROVE** | 15/15 sampled supported, 0 exceptions, 0 evidence errors |
| CoolMOS (207) | **RECOMMEND APPROVE** | 21/21 (incl. all 6 duplicate-doc + 8 series-stratum), 0 exceptions |
| CoolSiC (127) | **RECOMMEND APPROVE** | 15/15, 0 exceptions |
| StrongIRFET (49) | **RECOMMEND APPROVE** | 26/26 pass-2 (all 5 A-stratum, all 6 B-stratum), harness defect corrected and disclosed |

No systematic membership defect found; no packet held.

## 7. Owner decision block (awaiting signature — nothing promoted)

- [ ] OptiMOS packet `7a65e880…` — approve / reject / defer: ______
- [ ] CoolMOS packet `071ec975…` — approve / reject / defer: ______
- [ ] CoolSiC packet `b2e4a60b…` — approve / reject / defer: ______
- [ ] StrongIRFET packet `701c45b6…` — approve / reject / defer: ______
- Approval reference (mail/log id): ______

On signature, promotion is mechanical and auditable:
`identity.promote(con, family_id, approval="<ref>")` — the only code path
to `verified`. Recommended first action after promotion: re-run
`facet_journeys.py` and the R6 suite (no code change needed; status flows
through snapshots automatically).
