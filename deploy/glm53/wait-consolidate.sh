#!/usr/bin/env bash
set -euo pipefail

[[ "$(hostname -s)" == "spark-49af" ]] || {
  printf 'run this script on DGX2 (spark-49af)\n' >&2
  exit 1
}

labels=(set0 set1 set2 set3)
targets=(
  ""
  "samkimasus1@100.124.181.13"
  "samkim3@100.73.119.63"
  "samkimasus3@100.89.118.36"
)
markers=(
  "/srv/models/GLM-5.3-Int4-Int8Mix/HARNESS_SHARD_SET_set0"
  "/home/samkimasus1/glm53-model-stage/HARNESS_SHARD_SET_set1"
  "/home/samkim3/glm53-model-stage/HARNESS_SHARD_SET_set2"
  "/home/samkimasus3/glm53-model-stage/HARNESS_SHARD_SET_set3"
)

run_rank() {
  local rank="$1"
  shift
  local command="$*"
  if [[ "$rank" == "0" ]]; then
    bash -lc "$command"
  else
    ssh -o BatchMode=yes -o ConnectTimeout=10 \
      "${targets[$rank]}" "bash -lc $(printf '%q' "$command")"
  fi
}

while :; do
  remaining=0
  for rank in 0 1 2 3; do
    if run_rank "$rank" "test -f '${markers[$rank]}'"; then
      state=complete
    else
      run_rank "$rank" \
        "unit_state=\$(systemctl show -p ActiveState --value 'glm53-range-${labels[$rank]}.service' 2>/dev/null); [[ \"\$unit_state\" == active || \"\$unit_state\" == activating ]]" ||
        {
          printf '%s failed before writing its completion marker\n' \
            "${labels[$rank]}" >&2
          exit 1
        }
      state=running
      remaining=$((remaining + 1))
    fi
    printf '%s %s=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      "${labels[$rank]}" "$state"
  done
  ((remaining > 0)) || break
  sleep 60
done

mac_marker="/srv/models/GLM-5.3-Int4-Int8Mix/HARNESS_SHARD_SET_set4"
while [[ ! -f "$mac_marker" ]]; do
  printf '%s set4=waiting-for-M5-transfer\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  sleep 60
done

"$HOME/glm53-tp4/consolidate-shards.sh"
