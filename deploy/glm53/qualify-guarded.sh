#!/usr/bin/env bash
set -uo pipefail

die() {
  printf 'GLM-5.3 guarded qualification: %s\n' "$*" >&2
  exit 2
}

[[ "$(hostname -s)" == "spark-49af" ]] ||
  die "run this script on DGX2 (spark-49af)"

root="$HOME/glm53-tp4"
archive="/srv/models/archives/pre-glm53-20260910T0342Z/qualification"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
guard_start="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
log="$archive/qualify-$stamp.log"
mkdir -p "$archive"

targets=(
  ""
  "samkimasus1@100.124.181.13"
  "samkim3@100.73.119.63"
  "samkimasus3@100.89.118.36"
)
ssh_opts=(
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o StrictHostKeyChecking=accept-new
)

run_rank() {
  local rank="$1"
  shift
  local command="$*"
  if [[ "$rank" == "0" ]]; then
    bash -lc "$command"
  else
    ssh "${ssh_opts[@]}" "${targets[$rank]}" \
      "bash -lc $(printf '%q' "$command")"
  fi
}

sample_rank() {
  local rank="$1"
  run_rank "$rank" \
    "mem=\$(awk '/MemAvailable/{print \$2}' /proc/meminfo); swap=\$(awk '/^pswpout /{print \$2}' /proc/vmstat); state=\$(docker inspect -f '{{.State.Running}}' glm53-tp4 2>/dev/null || echo absent); faults=\$(sudo -n journalctl -k --since '$guard_start' --no-pager 2>/dev/null | grep -Eic 'NVRM.*Xid|oom-kill|Out of memory: Killed process' || true); printf '%s %s %s %s\n' \"\$mem\" \"\$swap\" \"\$state\" \"\$faults\""
}

abort_qualification() {
  local reason="$1"
  printf '\nABORT %s\n' "$reason" | tee -a "$log" >&2
  kill -TERM "$qualify_pid" 2>/dev/null || true
  wait "$qualify_pid" 2>/dev/null || true
  "$root/orchestrate.sh" stop >>"$log" 2>&1 || true
  exit 1
}

printf 'qualification started %s args=' "$stamp" | tee "$log"
printf '%q ' "$@" | tee -a "$log"
printf '\n' | tee -a "$log"

"$root/qualify.py" "$@" >>"$log" 2>&1 &
qualify_pid=$!
trap 'kill -TERM "$qualify_pid" 2>/dev/null || true' INT TERM

declare -a low_count=(0 0 0 0)
declare -a previous_swap=(-1 -1 -1 -1)
while kill -0 "$qualify_pid" 2>/dev/null; do
  for rank in 0 1 2 3; do
    read -r mem_kib swap_pages state faults < <(sample_rank "$rank") ||
      abort_qualification "rank $rank became unreachable"
    printf '%s rank=%s available=%sMiB pswpout=%s state=%s faults=%s\n' \
      "$(date -u +%H:%M:%S)" "$rank" "$((mem_kib / 1024))" \
      "$swap_pages" "$state" "$faults" | tee -a "$log"

    [[ "$state" == "true" ]] ||
      abort_qualification "rank $rank container is not running"
    ((faults == 0)) ||
      abort_qualification "rank $rank logged a GPU Xid or OOM fault"

    if ((mem_kib < 4096 * 1024)); then
      low_count[$rank]=$((low_count[$rank] + 1))
    else
      low_count[$rank]=0
    fi
    ((low_count[$rank] < 2)) ||
      abort_qualification "rank $rank stayed below 4 GiB available"

    if ((previous_swap[$rank] >= 0)); then
      delta=$((swap_pages - previous_swap[$rank]))
      ((delta <= 131072)) ||
        abort_qualification "rank $rank swapped out more than 512 MiB in 20 seconds"
    fi
    previous_swap[$rank]="$swap_pages"
  done
  sleep 20
done

wait "$qualify_pid"
result=$?
cat "$log"
if ((result != 0)); then
  printf 'qualification failed; stopping every GLM rank\n' | tee -a "$log" >&2
  "$root/orchestrate.sh" stop >>"$log" 2>&1 || true
fi
exit "$result"
