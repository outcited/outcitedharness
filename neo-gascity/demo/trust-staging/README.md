# Staging trust-root fixtures

`*.template.json` files are the INTENTIONAL, committed fixtures: gate_demo.py
fills `payload_digest` at run time and writes the concrete artifact here.
Everything else in this directory is generated per run (forged/nodigest/
wrongcity/wrongproj negative controls, gate/e2e/live artifacts) and is
gitignored.

These are STAGING fixtures (`issuer: staging-fixture`, `environment:
staging`). Nothing in this repository is or represents a production
approval. Production roots are registered by the operator via
GC_INGEST_TRUST_PROD and must be owned by a non-service uid — see
bridge/handoff.py and schemas/README.md.
