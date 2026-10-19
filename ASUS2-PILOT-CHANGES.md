# ============================================================================
# ASUS2 REASONING PILOT — exact changes for Sam's review
# NOT APPLIED YET. These are the precise diffs.
#
# Change 1: run-observer.sh — remove --no-llm (line 28)
# Change 2: policy.yaml — restrict to asus2 only (pilot scope)
# ============================================================================

## Change 1: run-observer.sh
# BEFORE (line 28):
#   --no-llm \
#
# AFTER:
#   (line removed entirely — LLM enabled with policy-scoped routing)
#
# Effect: the observer constructs OpsBrainClient with ModelPolicy.
#   Without --no-llm, self.llm is set, and run_code_intel fires
#   when it finds P0/P1 assessments.

## Change 2: /etc/harness-director/designwins/policy.yaml
# BEFORE (models.allowed section):
#   5 endpoints: asus2, dgx1_overflow, tp4_dsv41, glm53_sub, mimo_pro
#
# AFTER (pilot — asus2 ONLY):
#   models:
#     allowed:
#       - name: asus2_ops_brain
#         provider: local
#         base_url: http://100.68.133.1:8900
#         model: qwen3.8-27b-nvfp4
#         role: pilot-reasoning-only
#     # PILOT: all other endpoints disabled during ASUS2 reasoning pilot.
#     # Re-enable after pilot review. dgx1 is offline (RMA) anyway.
#     # tp4_dsv41, glm53_sub, mimo_pro commented out per Gate 2.
#     # - name: dgx1_overflow      # offline for RMA
#     # - name: tp4_dsv41           # disabled for pilot
#     # - name: glm53_sub           # disabled for pilot (subscription)
#     # - name: mimo_pro            # disabled for pilot (subscription)

## What does NOT change:
#   - delivery policy (still enabled=false unless approval artifact matches)
#   - authorization (same approvals dir, same operator uid)
#   - metered blocklist (unchanged — openrouter/metered still hard-refused)
#   - GCI token (already staged)
#   - feeds, workspace, all other config

## Expected behavior after these two changes:
#   1. Observer tick runs every 600s
#   2. run_code_intel fires if there are P0/P1 assessments in the journal
#   3. GCI search retrieves code context with file/line/SHA citations
#   4. ASUS2 (100.68.133.1:8900, qwen3.8-27b-nvfp4) receives the context
#   5. Model responds with investigation reasoning
#   6. Result journaled with code_intel_investigation field
#   7. verified: false (structurally enforced — model output never
#      becomes verified fact)
#   8. If ASUS2 is down, observation continues without reasoning (no-op)
#   9. If no P0/P1 findings, no reasoning call is made
#  10. Rate limit: first 3 P0/P1 findings per cycle (already bounded)

## Rollback: add --no-llm back, restore policy.yaml, restart. One command.
