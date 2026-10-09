# Embedder standby (asus2) — restore + relocation recipe

Proven end-to-end 2026-10-09. This directory is the recipe that makes the
production embedder recoverable on a box other than the protected one.

## What production is

| | |
|---|---|
| Box | dgx1 / `spark-e10b`, `100.81.201.24` (fabric `10.77.0.7`) |
| Service | `bge-m3-embed.service` — `uvicorn bge_m3_service:app`, `Restart=always`, `enabled` |
| Port | `:8800` (`/v1/embeddings` `/embed` `/encode` `/semantic-search`, health `GET /healthz`) |
| Weights | FAE v4 fine-tune reached via `models/bge-m3-cr-tapes-v1/latest` → `models/bge-m3-fae-search-v4/checkpoints_20260822T045420Z` |
| API label | `bge-m3-cr-tapes-v1` (legacy; the label never changed when the weights did) |
| Consumers | GCI on the same box at `127.0.0.1:8800`; config keys `spark_embed` / `spark_embedder`; GCI clients reach embeddings via `:8810` |
| Backup | all four generations on the NAS: `/Volumes/M5_4TB/harness-training/models/` |

## What was proven

A standby was built on **asus2** from weights pulled over the fabric from e10b
(never relayed through the M5 — RUNBOOK network rule), started on
`127.0.0.1:8801`, and compared against production:

```
dims: production=1024 rehearsal=1024
model label: production=bge-m3-cr-tapes-v1 rehearsal=bge-m3-cr-tapes-v1
texts compared: 8
bit-identical vectors: 8/8
max abs component diff: 0.000e+00
min cosine similarity:  1.000000000000
VERDICT: IDENTICAL
```

So: the backup restores, the restored service is **byte-equivalent** to
production, and the embedder is not bolted to the protected box. Production was
undisturbed throughout — its `/healthz` uptime kept climbing (38,887 s across
the test) and GCI never missed a refresh.

## What is left on asus2 (cold standby, ~15G)

```
~/embedder-rehearsal/
  weights/bge-m3-fae-search-v4/checkpoints_20260822T045420Z/   15G
  venv/          116M  (--system-site-packages; reuses the box's torch 2.11.0+cu130)
  app/bge_m3_service.py                                        copied from e10b
  serve.log
```

Not running, **no cron, no `@reboot` entry** — it starts only when someone runs
`start-standby.sh`. asus2's crontab is empty and should stay that way.

## Restore / relocation procedure

1. Get the weights onto the target box. Prefer fabric, node-to-node:
   `rsync -a samkim@10.77.0.7:/data/categoryrank/cr-finetune/models/bge-m3-fae-search-v4/ <dest>/`
   (asus2 → e10b key auth already works). From the NAS instead if e10b is the
   box that died — the NAS is Mac-attached, so that path rides the M5.
2. Copy the service source: `scp samkim@10.77.0.7:/data/categoryrank/cr-finetune/bge_m3_service.py <app>/`
   Note `rsync -a` of a *single file* into a directory silently produced nothing
   here (openrsync); `scp` worked. Verify the file exists before continuing.
3. Build the venv: `python3 -m venv --system-site-packages <venv>` then
   `pip install FlagEmbedding==1.4.0`. Production runs FlagEmbedding 1.4.0 +
   torch 2.11.0; `--system-site-packages` reuses the box's torch instead of
   re-downloading gigabytes.
4. Start it: `./start-standby.sh 8801`. Binds `127.0.0.1` only and refuses
   `:8800` — both deliberate (see Guardrails).
5. **Prove equivalence before anyone trusts it:**
   `python3 verify_equivalence.py --candidate http://127.0.0.1:8801/v1/embeddings`
   (run it on the target box; production is reachable over the fabric at
   `http://10.77.0.7:8800/v1/embeddings`). Exit 0 = bit-identical.
6. Only then repoint anything. Repointing GCI means moving GCI too — see below.

## Guardrails

- **Never bind the standby to `0.0.0.0`, never on `:8800`.** On 2026-10-09 asus2
  ran a *stock* `BAAI/bge-m3` on `0.0.0.0:8800` with an `@reboot` cron: same
  port, same 1024 dims, different vectors, and no config pointed at it — one
  `base_url` edit from silently invalidating every GCI vector. Removed with
  Sam's approval. `start-standby.sh` refuses `:8800` so this cannot recur by
  accident.
- **Identity is `/healthz`, not `/v1/models`.** The service 404s on
  `/v1/models`; a stock clone 404s on `/healthz`. `/healthz` also returns
  `model_path`, which is the only thing that reveals *which generation* is
  loaded — the API label has said `bge-m3-cr-tapes-v1` since April while the
  weights moved to v4 in August.
- **Equivalence is bit-identity, not cosine.** `verify_equivalence.py` fails
  unless every vector matches exactly; cosine 0.9999 would hide a different
  checkpoint of the same family.

## Relocating GCI with it (unblocked 2026-10-09)

GCI's encoder used to be hard-pinned to `http://127.0.0.1:8800/v1/embeddings`
(`harness/gci/encoder.py`), so a standby embedder gave you vectors but not
search. That policy is now an **explicit allowlist**:

```
HARNESS_GCI_ENCODER_ALLOWLIST=10.77.0.5:8801      # comma-separated host:port
```

Unset (the default) means the historic localhost-only behaviour — the suite
asserts that `http://100.81.201.24:8800/v1/embeddings` is still refused. The
allowlist widens *nothing else*: scheme must stay `http`, path must stay
`/v1/embeddings`, query and fragment are still rejected, redirects are still
refused, and entries match exactly (no wildcards, host and port both required).
7 tests in `tests/test_gci_indexer.py` hold this.

So a full relocation of the pair is:

1. This recipe on the target box, proven `IDENTICAL` by `verify_equivalence.py`.
2. The GCI service — `deploy/harness-gci.service`, `/etc/harness-gci.env`, index
   at `/data/harness-gci` (343M) — on the **same** box.
3. `HARNESS_GCI_ENCODER_ALLOWLIST` set to that box's encoder host:port.
4. `gci_url` in `config/settings.yaml` repointed. Note GCI binds only to the
   address it is given: on e10b it listens on `100.81.201.24:8810` and *not*
   `0.0.0.0`, so `127.0.0.1:8810` gives HTTP 000 even when healthy.
5. `GET /health` on the new GCI → 401 = alive (bearer-guarded).

Only then is the protected box replaceable. Until steps 2-5 happen, the standby
is a *weights + service* recovery path, not a search failover.
