# GLM-5.3 flagship TP4+DCP4 deployment

This deployment serves the full 743B/~40B-active GLM-5.3 model, not
GLM-5.3-Flash:

- checkpoint: `Tech2wild/GLM-5.3-Int4-Int8Mix`
- format: compressed-tensors, W4A16 experts plus W8A16 attention/dense layers
- topology: TP4+DCP4 on DGX2, ASUS1, DGX3, and ASUS3
- speculation: disabled in the qualified long-context profile
- context: 524,288 tokens with a 7.2 GB FP8 sparse-MLA KV pool per rank
- reasoning default: `max`
- endpoint: `http://10.77.0.1:8000/v1`, protected by the key on DGX2

The checkpoint is text-only. The three existing vision containers are stopped,
not deleted, while this service is active.

## Immutable inputs

Pins are recorded in `site.env`. The serving recipe is
`tonyd2wild/GLM-5.3-Int4-Int8Mix-TP4-4x-DGX-Spark` at the recorded revision.
The ten SM121 kernel overlays come from the recorded revision of
`tonyd2wild/GLM-5.2-QuantTrio-200K-4x-DGX-Spark`.

The DCP overlays are pinned to
`ajclark/GLM-5.3-DCP4-DFlash2-NVMe-KV-Offload-4x-DGX-Spark`. Only its
target-model TP4+DCP4 files are installed; the DFlash2 drafter and NVMe KV tier
are not used. The overlay contains explicit native-MTP verification paths and
tests even though its author's launcher disabled MTP as an operator choice.
The checksum-pinned local overlay also recognizes the installed SM121 Triton
paged-MQA kernel as multi-atom capable. Structured-output fixes from upstream
vLLM PRs
[#44297](https://github.com/vllm-project/vllm/pull/44297) and
[#44993](https://github.com/vllm-project/vllm/pull/44993) are backported because
the pinned June 19 image predates them.

The image is pulled by repository digest and then checked against its expected
content ID before being retagged locally. The model download is pinned by commit
and must pass Hugging Face cache verification before
`HARNESS_DOWNLOAD_VERIFIED` is created.

The verified 19.3 GB runtime archive is retained on DGX3 at
`/var/tmp/glm53-runtime-ab666069.tar`; its SHA-256 is
`99b1380cea629b7672808689c6d73ddfb058e796ab65838c9e845584e16a44bc`.
The 405.2 GB checkpoint is staged in four disjoint ranges with
`download-range.sh`, one resumable and finalized shard per range at a time.
`wait-consolidate.sh` then assembles the ranges and
`consolidate-shards.sh` strictly verifies the pinned revision on DGX2.

The GLM-5.3 weights use the upstream GLM-5.3 license. It permits use and
commercial deployment, with a security-review condition for a Model-as-a-Service
operator whose affiliated revenue exceeds US$10 billion in a 12-month period.

## Operation

Run these commands on DGX2:

```bash
~/glm53-tp4/orchestrate.sh preflight
~/glm53-tp4/orchestrate.sh drain-vision
~/glm53-tp4/orchestrate.sh start
~/glm53-tp4/qualify-guarded.sh --long-context-tokens 512000
```

`start` and `start-bare` both select the qualified graphless long-context
profile. Native MTP is retained only for investigation and requires both the
explicit `start-mtp-experimental` action and
`GLM53_ALLOW_EXPERIMENTAL_MTP=1`; it is not a production setting on four
128 GB nodes.
`GLM53_DEPLOYMENT_HOLD=1` blocks either launch while still allowing preflight;
set it to `0` only after all staged artifacts pass preflight.

Status and shutdown:

```bash
~/glm53-tp4/orchestrate.sh status
~/glm53-tp4/orchestrate.sh stop
```

Rollback to the preserved vision service:

```bash
~/glm53-tp4/orchestrate.sh restore-vision
```

Rollback assets and checksums are under
`/srv/models/archives/pre-glm53-20260910T0342Z` on DGX2.

## Qualified behavior

The active profile uses a 524,288-token maximum, two concurrent sequences,
2,048 batched prefill tokens, and a 7.2 GB fixed KV allocation.
DCP4 token-shards the otherwise replicated sparse-MLA KV cache across all four
ranks and provides 526,080 global token slots. Measured model loading uses
94.18 GiB per rank.

On September 10, 2026, a cache-independent seven-exchange request passed at
511,988 prompt tokens with exact three-code recall and `reasoning_effort=max`.
The request took 1,279.70 seconds. Rank 0 bottomed near 6.76 GiB available;
the other ranks remained above 8.3 GiB. There were no new swap-outs, GPU Xids,
OOMs, or worker exits. Short decode measured 9.69 tok/s for one request and
18.28 tok/s aggregate for two.

The experimental native-MTP4 profile loads 96.68 GiB per rank. It passed
required tool calling after the upstream grammar fixes, measured 35.3 tok/s
single-stream and 58.3 tok/s aggregate, and passed unique 131,068-token recall.
At 256K, however, rank 0 remained below the 4 GiB floor for two samples
(3.98 GiB low-water), so the guard canceled the request and stopped the
cluster. MTP is therefore not qualified for the 512K service on this hardware.

Measured startup from the shared model store is about 11–16 minutes. DCP4 adds
cross-rank collectives and trades throughput for the larger context pool.

The guarded qualification samples every rank every 20 seconds. It cancels the
request and stops GLM if a worker exits, a rank remains below 4 GiB available
for two samples, a rank swaps out more than 512 MiB in one interval, or the
kernel reports a GPU Xid or OOM fault.

GLM-5.3 supports request-level `reasoning_effort` values `low`, `high`, and
`max`. Production defaults to `max`; qualification uses `low` for bounded
smoke tests and `max` for its seven-exchange long-context retention test.
