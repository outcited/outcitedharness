#!/usr/bin/env bash
# review-gate.sh — run NEO's independent counterexample reviews against a head.
# Usage: review-gate.sh <repo-path> <rev> [script...]
# Default scripts: all vendored in review-scripts/ (each has REPO/REV hardcoded —
# the runner rewrites them via env substitution into a temp copy).
# Output: JSON per script in /tmp/review-gate/<rev>/ + a PASS/FAIL summary line.
# Non-destructive; synthetic fixtures only.
set -u
REPO="${1:?repo path}"; REV="${2:?rev}"; shift 2
SCRIPTS=("$@"); [ ${#SCRIPTS[@]} -eq 0 ] && SCRIPTS=("$(dirname "$0")"/../review-scripts/*.py)
OUT=/tmp/review-gate/$REV; mkdir -p "$OUT"
FAIL=0
for s in "${SCRIPTS[@]}"; do
  name=$(basename "$s" .py)
  tmps="$OUT/$name.run.py"
  sed -e "s#repo='[^']*'#repo='$REPO'#g" -e "s#rev='[^']*'#rev='$REV'#g" "$s" > "$tmps"
  echo "== $name @ $REV"
  PYBIN="${VENV_PY:-/Users/samsonkim/designwins-orchestrator/harness/.venv/bin/python3}"
  mkdir -p "$OUT/evidence/orchestrator-priorities-20261031"
  if (cd "$OUT" && "$PYBIN" "$tmps") > "$OUT/$name.json" 2>"$OUT/$name.err"; then
    echo "   ran clean -> $OUT/$name.json"
  else
    rc=$?
    echo "   EXIT $rc (see $OUT/$name.json + .err)"; FAIL=$((FAIL+1))
  fi
done
echo "GATE-SUMMARY rev=$REV failures=$FAIL"
exit $((FAIL > 0 ? 1 : 0))
