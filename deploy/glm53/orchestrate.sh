#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'GLM-5.3 orchestration: %s\n' "$*" >&2
  exit 2
}

[[ "$(hostname -s)" == "spark-49af" ]] ||
  die "run this script on DGX2 (spark-49af)"

action="${1:-status}"
container="glm53-tp4"
vision_container="qwen3-vl-30b-pin-gate-v4-vllm"
archive_root="/srv/models/archives/pre-glm53-20260910T0342Z"
key_file="$HOME/.secrets/glm53-key"
startup_in_progress=0
ssh_opts=(
  -o BatchMode=yes
  -o ConnectTimeout=15
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=4
  -o StrictHostKeyChecking=accept-new
)
targets=(
  ""
  "samkimasus1@100.124.181.13"
  "samkim3@100.73.119.63"
  "samkimasus3@100.89.118.36"
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

capture_vision_logs() {
  local stamp rank target
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  install -d -m 700 "$archive_root/vision-logs"
  for rank in 1 2 3; do
    target="${targets[$rank]}"
    ssh "${ssh_opts[@]}" "$target" \
      "docker logs --timestamps '$vision_container' 2>&1 || true" \
      >"$archive_root/vision-logs/rank${rank}-${stamp}.log"
  done
  sha256sum "$archive_root"/vision-logs/rank*-"$stamp".log \
    >"$archive_root/vision-logs/SHA256SUMS-$stamp"
}

drain_vision() {
  capture_vision_logs
  local rank
  for rank in 1 2 3; do
    printf 'stopping vision on rank %s\n' "$rank"
    run_rank "$rank" \
      "docker stop -t 120 '$vision_container' >/dev/null 2>&1 || true"
  done
  all_ranks \
    "if docker ps --format '{{.Names}}' | grep -qx '$vision_container'; then exit 1; fi"
}

prepare_memory() {
  all_ranks \
    "set -e; printf 'vm.swappiness=10\n' | sudo -n tee /etc/sysctl.d/90-harness-glm53.conf >/dev/null; sudo -n sysctl --system >/dev/null; test \"\$(cat /proc/sys/vm/swappiness)\" = 10; sync; echo 3 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null; echo 1 | sudo -n tee /proc/sys/vm/compact_memory >/dev/null"
}

start_flushers() {
  all_ranks "pkill -f '[f]lusher-unconditional.sh' 2>/dev/null || true"
  all_ranks \
    "setsid nohup \"\$HOME/glm53-tp4/flusher-unconditional.sh\" 5400 >\"\$HOME/glm53-tp4/flusher.log\" 2>&1 < /dev/null & pid=\$!; sleep 1; kill -0 \"\$pid\""
}

stop_flushers() {
  local rank failed=0
  for rank in 0 1 2 3; do
    run_rank "$rank" \
      "pkill -f '[f]lusher-unconditional.sh' 2>/dev/null || true" ||
      failed=1
  done
  return "$failed"
}

stop_glm() {
  local rank failed=0
  for rank in 0 1 2 3; do
    run_rank "$rank" \
      "if docker inspect '$container' >/dev/null 2>&1; then docker stop -t 120 '$container' >/dev/null; fi" ||
      failed=1
  done
  return "$failed"
}

stop_all() {
  local failed=0
  stop_glm || failed=1
  stop_flushers || failed=1
  return "$failed"
}

remove_glm() {
  local rank failed=0
  for rank in 0 1 2 3; do
    run_rank "$rank" \
      "if docker inspect '$container' >/dev/null 2>&1; then docker rm -f '$container' >/dev/null; fi" ||
      failed=1
  done
  return "$failed"
}

capture_glm_logs() {
  local stamp rank
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  install -d -m 700 "$archive_root/glm-logs"
  for rank in 0 1 2 3; do
    run_rank "$rank" "docker logs --timestamps '$container' 2>&1 || true" \
      >"$archive_root/glm-logs/rank${rank}-${stamp}.log"
  done
  sha256sum "$archive_root"/glm-logs/rank*-"$stamp".log \
    >"$archive_root/glm-logs/SHA256SUMS-$stamp"
}

cluster_containers_running() {
  local rank
  for rank in 0 1 2 3; do
    run_rank "$rank" \
      "test \"\$(docker inspect -f '{{.State.Running}}' '$container' 2>/dev/null)\" = true" \
      >/dev/null || return 1
  done
}

cluster_containers_stopped() {
  local rank
  for rank in 0 1 2 3; do
    run_rank "$rank" \
      "test \"\$(docker inspect -f '{{.State.Running}}' '$container' 2>/dev/null || echo absent)\" != true" \
      >/dev/null || return 1
  done
}

cluster_flushers_running() {
  local rank
  for rank in 0 1 2 3; do
    run_rank "$rank" \
      "pgrep -f '[f]lusher-unconditional.sh' >/dev/null" ||
      return 1
  done
}

start_glm() {
  local profile="${1:-production}"
  local launch_env
  case "$profile" in
    production)
      [[ "${GLM53_ALLOW_EXPERIMENTAL_MTP:-0}" == "1" ]] ||
        die "MTP profile is experimental and did not retain 4 GiB at 256K"
      launch_env="GLM53_ALLOW_EXPERIMENTAL_MTP=1 GLM53_LAUNCH_PROFILE=production"
      ;;
    bare)
      launch_env="GLM53_LAUNCH_PROFILE=bare"
      ;;
    *) die "unknown start profile: $profile" ;;
  esac

  preflight
  startup_in_progress=1
  [[ -s "$key_file" ]] || die "head API key is missing: $key_file"
  remove_glm
  prepare_memory
  start_flushers

  local rank
  for rank in 3 2 1 0; do
    printf 'launching rank %s (%s)\n' "$rank" "$profile"
    run_rank "$rank" \
      "$launch_env \"\$HOME/glm53-tp4/launch-rank.sh\" \"$rank\""
    [[ "$rank" != "0" ]] && sleep 5
  done

  printf 'waiting for authenticated health endpoint'
  local waited=0
  until curl -fsS --max-time 15 \
    -H "Authorization: Bearer $(<"$key_file")" \
    "http://127.0.0.1:8000/health" >/dev/null; do
    if ! cluster_containers_running; then
      printf '\n'
      capture_glm_logs
      die "a GLM rank exited during startup; logs were archived"
    fi
    if ! cluster_flushers_running; then
      printf '\n'
      capture_glm_logs
      die "a page-cache flusher exited during startup; logs were archived"
    fi
    sleep 30
    waited=$((waited + 30))
    printf '.'
    if (( waited >= 4500 )); then
      printf '\n'
      capture_glm_logs
      die "GLM did not become healthy within 4500 seconds"
    fi
  done
  printf ' healthy after %ss\n' "$waited"
  cluster_containers_running || {
    capture_glm_logs
    die "health passed but a GLM rank is not running"
  }
  if [[ "$profile" == "production" ]] &&
    ! docker logs "$container" 2>&1 |
      grep -F "use_flattening=False (next_n=5" >/dev/null; then
    capture_glm_logs
    die "production started without native SM121 MTP next_n=5"
  fi
  stop_flushers
  startup_in_progress=0
}

restore_vision() {
  stop_all
  cluster_containers_stopped ||
    die "refusing to restore vision while a GLM rank is still running"
  local rank
  for rank in 1 2 3; do
    printf 'restoring vision on rank %s\n' "$rank"
    run_rank "$rank" "docker start '$vision_container' >/dev/null"
    run_rank "$rank" \
      "test \"\$(docker inspect -f '{{.State.Running}}' '$vision_container')\" = true"
    run_rank "$rank" \
      "for attempt in \$(seq 1 60); do curl -fsS --max-time 5 http://127.0.0.1:8912/health >/dev/null && exit 0; sleep 5; done; exit 1"
  done
}

preflight() {
  local rank
  for rank in 0 1 2 3; do
    printf '\n== rank %s ==\n' "$rank"
    run_rank "$rank" \
      "GLM53_PREFLIGHT_ONLY=1 \"\$HOME/glm53-tp4/launch-rank.sh\" \"$rank\""
  done
}

status() {
  all_ranks \
    "hostname; docker ps -a --filter name='$container' --filter name='$vision_container' --format '{{.Names}}|{{.Status}}|{{.Image}}'; awk '/MemAvailable|SwapFree/{printf \"%s=%dMiB \", \$1, \$2/1024} END{print \"\"}' /proc/meminfo; nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw --format=csv,noheader,nounits"
}

cleanup_failed_startup() {
  local rc=$?
  trap - EXIT
  if ((startup_in_progress == 1 && rc != 0)); then
    set +e
    printf 'startup failed; stopping every GLM rank\n' >&2
    stop_all
  fi
  exit "$rc"
}
trap cleanup_failed_startup EXIT

case "$action" in
  preflight) preflight ;;
  drain-vision) drain_vision ;;
  start-bare) start_glm bare ;;
  start) start_glm bare ;;
  start-mtp-experimental) start_glm production ;;
  stop) stop_all ;;
  restore-vision) restore_vision ;;
  status) status ;;
  *)
    die "usage: orchestrate.sh {preflight|drain-vision|start|start-bare|start-mtp-experimental|stop|restore-vision|status}"
    ;;
esac
