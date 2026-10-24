# Least-privilege access REQUEST (for credential owner — no changes made by m5-opencode)

Per LEAST-PRIVILEGE-PROPOSAL.md, requesting these SEPARATE tokens:
1. m5-opencode: contents:read on outcited/cr-core + M4 FE repo (fixture acceptance) — READ ONLY
2. Dedicated review token: issues:write + PR review on outcited/outcitedharness ONLY (enables PR #20 reviewer assignment; current token lacks read:org)
3. M4 FE team: their own repo issues:write (fixture receipt thread #72)
4. cr-core team: cr-core issues:write + branch push (ingestion thread #103)
NOT requested: dispatch, deployment, production/ExportStore, org-wide scopes, token expansion on existing credentials.
