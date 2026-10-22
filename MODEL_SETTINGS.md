# Model Endpoint Settings — the law (verified live 2026-10-03)

Three reasoning-mode bugs tonight came from guessing these. They are now
measured. DO NOT change without a live test. If a model returns empty
content, check here before debugging anything else.

## DeepSeek V4.1-Flash — reasoning control (measured 2026-10-03)

The model reasons by default (the fleet recipe pins
`SGLANG_DSV41_REASONING_EFFORT=75` server-side). The switch differs by path:

| Path | REQUIRED setting | Measured |
|---|---|---|
| **Local sglang (TP4)** | `chat_template_kwargs: {"thinking": false}` — honored; recipe's own smoke test uses exactly this | works (recipe line 766) |
| **OpenRouter cloud** | `reasoning: {"effort": "low"}` + `max_tokens: 16000` | 57 claims/46s, $0.004/doc |
| OpenRouter + `chat_template_kwargs` | **IGNORED by provider** — content stays empty, 6000 tokens burned on hidden thinking | measured fail |
| OpenRouter, no settings | same failure — all budget consumed as reasoning | measured fail |

Rule: the budget must hold thinking + answer, or thinking must be off.
Through OpenRouter you cannot turn it off, only shrink it — hence 16K.

## Local canon (Qwen3.8-27B-FP8, sglang, asus4:8900)

- REQUIRED: `chat_template_kwargs: {"enable_thinking": false}` — cuts
  verdict calls 23s -> 5s.
- Verdict-level deterministic (5/5 identical across batch-varied reruns).

## Local student (sglang)

- max_tokens >= 6000: trained outputs are long JSON arrays; 2000 truncates.
- Truncation repair: close at last complete object.

## Local V4.1 (TP4, dgx2:8888)

- No thinking issues observed; content always populated.
- Proposer itself is NOT deterministic at temp 0 under serving (batched
  kernels) — proposals vary call-to-call; verification is per-proposal so
  this costs rework, never correctness.

## The rule

Every new model endpoint gets a settings row HERE after its first live
verification, before it enters any pipeline. Empty content = settings, not
philosophy.

## MiMo cloud vision (token-plan-sgp, owner account, FREE + unlimited)

Endpoint `https://token-plan-sgp.xiaomimimo.com/v1` (key: `MIMO_API_KEY`
in .env). Models: mimo-v2.6-flash (volume), mimo-v2.6-pro. Role: **volume
vision lane** — deterministic gates (grounding, canonicalize, anchor, gold
scorer) equalize model quality; a weak read holds, never ships wrong.

Measured (2026-10-06, gold = 3 plots / 40 points, same scorer as DeepSeek):

| | mimo-v2.6-flash | DeepSeek TP4 |
|---|---|---|
| series-count exact | 1.0 | 1.0 |
| point error mean / max | 0.83% / 3.0% | 0.72% / 4.0% |
| axis-label key match | 1.0 | 1.0 |
| axis-label verbatim | 0.0-0.33 | 1.0 |

Digitization is DeepSeek-class; the only gap is verbatim label spelling
(unit suffixes, μ/µ, subscript normalization) — repaired by
`typical_curves.canonicalize_labels` (deterministic word-run joins) or
covered by the label-key match metric.

REQUIRED settings (all measured):

1. **max_tokens >= 16000** — the provider burns hidden reasoning tokens
   first and returns EMPTY content otherwise (measured: 4095 reasoning /
   4096 budget at max_tokens 4096, content len 0). Same failure class as
   OpenRouter DeepSeek above: the budget must hold thinking + answer.
1b. **`reasoning_effort: "low"` REQUIRED** — lowest setting the provider
   honors; thinking cannot be disabled, only shrunk. Measured (same probe,
   same image): low=120 reasoning tokens / 534 completion; baseline=147-
   1030 (task-varies); `minimal` = HTTP 400; `enable_thinking:false` = 310
   (worse); `chat_template_kwargs:{thinking:false}` = 1322 (much worse —
   do NOT copy the local-sglang recipe here, this provider reads it
   differently). Never leave reasoning uncontrolled on vision calls.
2. **Do not embed the JSON schema in the prompt** — the model echoes the
   schema document back as `{"properties": {...}}` wrapper. Prose key
   description + the deterministic `validate_response` gate instead
   (CloudVisionClient unwraps defensively).
3. Point form varies (`[x,y]` pairs vs `{x,y}` objects) — normalize before
   validation (`_normalize_curve_points`).
4. Latency: ~80s per plot crop at 200 DPI; image ~620 tokens.

## ENGINE ROUTING LAW (Sam ruling, 2026-10-21 — DO NOT OVERRIDE)

**Cloud subscriptions are PRIMARY for volume extraction. TP4 is ADJUDICATION-ONLY.**

| engine | role | rate limit | cost |
|---|---|---|---|
| **QwenCloud DSV41** (`token-plan.maas.qwencloudapi.com`) | PRIMARY for ALL text extraction (readers, classification, table extraction, binding) | subscription = unlimited | tokens |
| **MiMo** (`token-plan-sgp.xiaomimimo.com`) | PRIMARY for ALL vision extraction (images, drawings, raster curves) | free, unlimited | free |
| **TP4** (`dgx2:8888`) | ADJUDICATION ONLY — genuinely ambiguous cases the cloud rejects or needs 1M context | 8 concurrent | local GPU |
| **OpenRouter** | OVERFLOW when cloud subscriptions are throttled | pay-per-doc | $0.004/doc |

**Why:** measured 2026-10-21 — QwenCloud 1.9s/call unlimited parallel vs TP4 8-request ceiling.
Volume extraction on TP4 starves every other lane. This rule exists because an agent
routed reader-v3 through TP4 and bottlenecked 6 boxes on one GPU cluster.

**Code defaults MUST point to QwenCloud.** TP4 is explicitly opted into, never the default.
