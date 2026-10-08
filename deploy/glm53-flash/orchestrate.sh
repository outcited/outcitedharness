#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'GLM-5.3-Flash orchestration: %s\n' "$*" >&2
  exit 2
}

[[ "$(hostname -s)" == "spark-49af" ]] ||
  die "run this script on DGX2 (spark-49af)"

action="${1:-status}"
root_rel="glm53-flash-tp4"
flash_container="glm53-flash-tp4"
flagship_container="glm53-tp4"
vision_container="qwen3-vl-30b-pin-gate-v4-vllm"
key_file="$HOME/.secrets/glm53-key"
site_env_path="/mnt/models/deploy/glm53-flash-site.env"
archive_root="/srv/models/archives/glm53-flash-cutover"
startup_in_progress=0
flagship_was_running=0
ssh_opts=(
  -o BatchMode=yes
  -o ConnectTimeout=15
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=4
  -o StrictHostKeyChecking=accept-new
)
targets=(
  ""
  "samkimasus1@10.77.0.2"
  "samkim3@10.77.0.3"
  "samkimasus3@10.77.0.4"
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

all_ranks() {
  local command="$*"
  local rank
  for rank in 0 1 2 3; do
    printf '\n== rank %s ==\n' "$rank"
    run_rank "$rank" "$command"
  done
}

cluster_running() {
  local container="$1"
  local rank
  for rank in 0 1 2 3; do
    run_rank "$rank" \
      "test \"\$(docker inspect -f '{{.State.Running}}' '$container' 2>/dev/null)\" = true" \
      >/dev/null || return 1
  done
}

cluster_flushers_running() {
  local rank
  for rank in 0 1 2 3; do
    run_rank "$rank" \
      "pgrep -f '[g]lm53-flash-tp4/flusher-unconditional.sh' >/dev/null" ||
      return 1
  done
}

stop_container() {
  local container="$1"
  local rank failed=0
  for rank in 0 1 2 3; do
    run_rank "$rank" \
      "if docker inspect '$container' >/dev/null 2>&1; then docker stop -t 120 '$container' >/dev/null; fi" ||
      failed=1
  done
  return "$failed"
}

remove_flash() {
  local rank failed=0
  for rank in 0 1 2 3; do
    run_rank "$rank" \
      "if docker inspect '$flash_container' >/dev/null 2>&1; then docker rm -f '$flash_container' >/dev/null; fi" ||
      failed=1
  done
  return "$failed"
}

capture_container_logs() {
  local container="$1"
  local stamp rank output_dir
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  output_dir="$archive_root/$container-$stamp"
  install -d -m 700 "$output_dir"
  for rank in 0 1 2 3; do
    run_rank "$rank" "docker logs --timestamps '$container' 2>&1 || true" \
      >"$output_dir/rank${rank}.log"
  done
  sha256sum "$output_dir"/rank*.log >"$output_dir/SHA256SUMS"
}

stop_flushers() {
  local rank failed=0
  for rank in 0 1 2 3; do
    run_rank "$rank" \
      "pkill -f '[g]lm53-flash-tp4/flusher-unconditional.sh' 2>/dev/null || true" ||
      failed=1
  done
  return "$failed"
}

start_flushers() {
  stop_flushers
  all_ranks \
    "setsid nohup \"\$HOME/$root_rel/flusher-unconditional.sh\" 7200 >\"\$HOME/$root_rel/flusher.log\" 2>&1 < /dev/null & pid=\$!; sleep 1; kill -0 \"\$pid\""
}

prepare_memory() {
  all_ranks \
    "set -e; printf 'vm.swappiness=0\n' | sudo -n tee /etc/sysctl.d/91-harness-glm53-flash.conf >/dev/null; sudo -n sysctl -p /etc/sysctl.d/91-harness-glm53-flash.conf >/dev/null; sudo -n swapoff -a; sudo -n swapon -a; test \"\$(cat /proc/swaps | wc -l)\" -gt 1; sync; echo 3 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null; echo 1 | sudo -n tee /proc/sys/vm/compact_memory >/dev/null"
}

preflight() {
  local rank
  for rank in 0 1 2 3; do
    printf '\n== rank %s ==\n' "$rank"
    run_rank "$rank" \
      "GLM53F_PREFLIGHT_ONLY=1 \"\$HOME/$root_rel/launch-rank.sh\" '$rank'"
  done
}

flagship_request_counts() {
  [[ -s "$key_file" ]] || die "API key is missing: $key_file"
  curl -fsS --max-time 10 \
    -H "Authorization: Bearer $(<"$key_file")" \
    http://127.0.0.1:8000/metrics 2>/dev/null |
    awk '/vllm:num_requests_running\{/{r+=$NF} /vllm:num_requests_waiting\{/{w+=$NF} END{print r+0, w+0}'
}

start_flash() {
  # P0: check deployment hold BEFORE any cutover action
  [[ -f "$site_env_path" ]] || die "site env is missing: $site_env_path"
  source "$site_env_path"
  [[ "${GLM53F_DEPLOYMENT_HOLD:-1}" == "0" ]] ||
    die "deployment hold is active (GLM53F_DEPLOYMENT_HOLD!=0); refusing to start"

  # P0: validate all prerequisites before touching the flagship
  preflight
  all_ranks "sudo -n true" ||
    die "passwordless sudo is required on all ranks"

  # P1: drain properly — check both running AND waiting, sample multiple times
  if cluster_running "$flagship_container"; then
    printf 'flagship is running; draining...\n'
    for attempt in 1 2 3; do
      read -r running waiting < <(flagship_request_counts)
      printf '  sample %s: running=%s waiting=%s\n' "$attempt" "$running" "$waiting"
      if ((running == 0 && waiting == 0)); then break; fi
      sleep 15
    done
    if ((running > 0 || waiting > 0)) && [[ "${GLM53F_FORCE_CUTOVER:-0}" != "1" ]]; then
      die "flagship still has running=$running waiting=$waiting; retry after completion or set GLM53F_FORCE_CUTOVER=1"
    fi
    capture_container_logs "$flagship_container"
    printf 'stopping flagship on all four ranks\n'
    stop_container "$flagship_container"
    flagship_was_running=1
  fi

  startup_in_progress=1
  remove_flash
  prepare_memory
  start_flushers

  local rank
  for rank in 3 2 1 0; do
    printf 'launching Flash rank %s\n' "$rank"
    run_rank "$rank" "\"\$HOME/$root_rel/launch-rank.sh\" '$rank'"
    [[ "$rank" != "0" ]] && sleep 5
  done

  printf 'waiting for authenticated Flash endpoint'
  local waited=0
  until curl -fsS --max-time 15 \
    -H "Authorization: Bearer $(<"$key_file")" \
    http://127.0.0.1:8000/health >/dev/null; do
    if ! cluster_running "$flash_container"; then
      printf '\n'
      capture_container_logs "$flash_container"
      die "a Flash rank exited during startup; logs were archived"
    fi
    if ! cluster_flushers_running; then
      printf '\n'
      capture_container_logs "$flash_container"
      die "a page-cache flusher exited during startup; logs were archived"
    fi
    sleep 30
    waited=$((waited + 30))
    printf '.'
    if ((waited >= 6600)); then
      printf '\n'
      capture_container_logs "$flash_container"
      die "Flash did not become healthy within 110 minutes"
    fi
  done
  printf ' healthy after %ss\n' "$waited"
  cluster_running "$flash_container" ||
    die "endpoint is healthy but a Flash rank is absent"
  stop_flushers
  # P1: after health, watch every rank for a grace window. A silently
  # power-cycled rank (GB10 EC hard-off) must be detected in seconds, not at
  # the engine's 6-minute broadcast timeout; a dead rank archives logs and
  # cleanup_failed_startup stops the survivors and rolls back the flagship.
  liveness_watchdog 15
  startup_in_progress=0
}

liveness_watchdog() {
  local interval="${1:-15}" grace="${2:-900}"
  local deadline=$(($(date +%s) + grace)) rank running
  ( trap 'exit 0' TERM INT
    while :; do
      sleep "$interval"
      for rank in 0 1 2 3; do
        running="$(run_rank "$rank" \
          "docker inspect -f '{{.State.Running}}' '$flash_container' 2>/dev/null || echo absent" \
          2>/dev/null || echo unreachable)"
        if [[ "$running" != "true" ]]; then
          printf 'watchdog: rank %s is %s\n' "$rank" "$running" >&2
          capture_container_logs "$flash_container" || true
          exit 1
        fi
      done
      if (($(date +%s) >= deadline)); then
        printf 'watchdog: every rank alive for %ss; standing down\n' "$grace" >&2
        exit 0
      fi
    done
  ) &
  watchdog_pid=$!
  while sleep 15; do
    if ! kill -0 "$watchdog_pid" 2>/dev/null; then
      wait "$watchdog_pid"
      rc=$?
      if ((rc == 0)); then
        printf 'watchdog: every rank alive for %ss; standing down\n' "$grace"
      else
        die "liveness watchdog detected a dead rank; logs archived, survivors stopped"
      fi
      break
    fi
  done
}

status() {
  all_ranks \
    "hostname; docker ps -a --filter name='$flash_container' --filter name='$flagship_container' --filter name='$vision_container' --format '{{.Names}}|{{.Status}}|{{.Image}}'; awk '/MemAvailable|SwapFree/{printf \"%s=%dMiB \", \$1, \$2/1024} END{print \"\"}' /proc/meminfo; nvidia-smi --query-gpu=utilization.gpu,temperature.gpu,power.draw --format=csv,noheader,nounits"
}

restore_flagship() {
  printf 'attempting flagship rollback...\n' >&2
  stop_container "$flash_container" 2>/dev/null || true
  stop_flushers 2>/dev/null || true
  if ((flagship_was_running == 1)); then
    local rank
    for rank in 0 1 2 3; do
      run_rank "$rank" "docker start '$flagship_container' >/dev/null 2>&1 || true"
    done
    printf 'flagship containers restarted (verify health separately)\n' >&2
  fi
}

cleanup_failed_startup() {
  local rc=$?
  trap - EXIT
  if ((startup_in_progress == 1 && rc != 0)); then
    set +e
    printf 'startup failed; stopping every Flash rank\n' >&2
    stop_container "$flash_container"
    stop_flushers
    # P0: rollback to flagship on failed startup
    restore_flagship
  fi
  exit "$rc"
}
trap cleanup_failed_startup EXIT

case "$action" in
  preflight) preflight ;;
  start) start_flash ;;
  stop)
    stop_container "$flash_container"
    stop_flushers
    ;;
  status) status ;;
  *)
    die "usage: orchestrate.sh {preflight|start|stop|status}"
    ;;
esac
