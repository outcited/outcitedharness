#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'GLM-5.3-Flash rank launch: %s\n' "$*" >&2
  exit 2
}

rank="${1:-}"
[[ "$rank" =~ ^[0-3]$ ]] || die "usage: launch-rank.sh <0|1|2|3>"

expected_hosts=(spark-49af gx10-fc2e spark-69c8 gx10-0309)
host_ips=(10.77.0.1 10.77.0.2 10.77.0.3 10.77.0.4)
expected_host="${expected_hosts[$rank]}"
host_ip="${host_ips[$rank]}"
head_ip="${host_ips[0]}"

[[ "$(hostname -s)" == "$expected_host" ]] ||
  die "rank $rank must run on $expected_host, not $(hostname -s)"

site_env="${GLM53F_SITE_ENV:-/mnt/models/deploy/glm53-flash-site.env}"
[[ -f "$site_env" ]] || die "site configuration is missing: $site_env"
# shellcheck source=/dev/null
source "$site_env"

: "${GLM53F_DEPLOYMENT_HOLD:=1}"
: "${GLM53F_IMAGE_REF:?missing GLM53F_IMAGE_REF}"
: "${GLM53F_IMAGE_DIGEST:?missing GLM53F_IMAGE_DIGEST}"
: "${GLM53F_IMAGE_ID:?missing GLM53F_IMAGE_ID}"
: "${GLM53F_MODEL_REVISION:?missing GLM53F_MODEL_REVISION}"
: "${GLM53F_INDEXER_SHA256:?missing GLM53F_INDEXER_SHA256}"
: "${GLM53F_TEMPLATE_SHA256:?missing GLM53F_TEMPLATE_SHA256}"

model_host_path="/mnt/models/GLM-5.3-Flash-NVFP4-redhat"
model_path="/models/glm-5.3-flash-nvfp4"
drafter_host_path="/mnt/models/GLM-5.3-Flash-DFlash2"
drafter_container_path="/models/dflash2-draft"
root="$HOME/glm53-flash-tp4"
indexer_host_path="$root/sparse_attn_indexer_kpool.py"
cache_host_path="$HOME/.cache/glm53-flash-vllm"
container_name="glm53-flash-tp4"
flagship_container="glm53-tp4"
master_port="${GLM53F_MASTER_PORT:-29521}"
port="${GLM53F_PORT:-8000}"
kv_cache_memory="${GLM53F_KV_CACHE_MEMORY:-25769803776}"
max_model_len="${GLM53F_MAX_MODEL_LEN:-1048576}"
max_num_seqs="${GLM53F_MAX_NUM_SEQS:-6}"
max_num_batched_tokens="${GLM53F_MAX_NUM_BATCHED_TOKENS:-16384}"
mtp_tokens="${GLM53F_MTP_TOKENS:-4}"
dflash_tokens="${GLM53F_DFLASH_TOKENS:-7}"
cudagraph_mode="${GLM53F_CUDAGRAPH_MODE:-FULL_AND_PIECEWISE}"
reasoning_effort="${GLM53F_REASONING_EFFORT:-max}"
spec_mode="${GLM53F_SPEC_MODE:-mtp}"

case "$spec_mode" in
  mtp|dflash2) ;;
  *) die "unsupported spec mode: $spec_mode (mtp or dflash2)" ;;
esac
[[ "$max_model_len" == "1048576" ]] ||
  die "this deployment is pinned to the model-native 1M context"
[[ "$mtp_tokens" =~ ^[1-9][0-9]*$ ]] || die "invalid MTP token count"
[[ "$dflash_tokens" =~ ^[1-9][0-9]*$ ]] || die "invalid DFlash2 token count"
case "$cudagraph_mode" in
  NONE|FULL|FULL_AND_PIECEWISE) ;;
  *) die "unsupported cudagraph mode: $cudagraph_mode" ;;
esac
case "$reasoning_effort" in
  low|high|max) ;;
  *) die "reasoning effort must be low, high, or max" ;;
esac

[[ -f "$model_host_path/config.json" ]] ||
  die "model config is missing from $model_host_path"
[[ -f "$model_host_path/model.safetensors.index.json" ]] ||
  die "model weight index is missing"
[[ -f "$model_host_path/model_mtp.safetensors" ]] ||
  die "native MTP weights are missing"
[[ -f "$model_host_path/chat_template_mm.jinja" ]] ||
  die "multimodal reasoning template is missing"
[[ "$(<"$model_host_path/HARNESS_PINNED_REVISION")" == "$GLM53F_MODEL_REVISION" ]] ||
  die "model revision does not match the site pin"
[[ -f "$model_host_path/HARNESS_DOWNLOAD_VERIFIED" ]] ||
  die "model download has not passed the verification gate"

drafter_mount_args=()
if [[ "$spec_mode" == "dflash2" ]]; then
  : "${GLM53F_DRAFT_REVISION:?missing GLM53F_DRAFT_REVISION}"
  : "${GLM53F_DRAFT_SHA256:?missing GLM53F_DRAFT_SHA256}"
  [[ -f "$drafter_host_path/config.json" ]] ||
    die "DFlash2 drafter config is missing: $drafter_host_path"
  [[ -f "$drafter_host_path/model.safetensors" ]] ||
    die "DFlash2 drafter weights are missing: $drafter_host_path"
  [[ -f "$drafter_host_path/HARNESS_DOWNLOAD_DONE" ]] ||
    die "DFlash2 drafter download has not completed"
  [[ "$(<"$drafter_host_path/HARNESS_PINNED_REVISION")" == "$GLM53F_DRAFT_REVISION" ]] ||
    die "DFlash2 drafter revision does not match the site pin"
  [[ "$(sha256sum "$drafter_host_path/model.safetensors" | cut -d ' ' -f 1)" == \
    "$GLM53F_DRAFT_SHA256" ]] ||
    die "DFlash2 drafter checksum mismatch"
  drafter_mount_args=(--mount "type=bind,src=$drafter_host_path,dst=$drafter_container_path,readonly")
fi

shopt -s nullglob
model_shards=("$model_host_path"/model-*.safetensors)
[[ "${#model_shards[@]}" == "10" ]] ||
  die "expected 10 model shards, found ${#model_shards[@]}"

[[ "$(sha256sum "$indexer_host_path" | cut -d ' ' -f 1)" == \
  "$GLM53F_INDEXER_SHA256" ]] ||
  die "sparse-indexer overlay checksum mismatch"
[[ "$(sha256sum "$model_host_path/chat_template_mm.jinja" | cut -d ' ' -f 1)" == \
  "$GLM53F_TEMPLATE_SHA256" ]] ||
  die "chat template checksum mismatch"

actual_image_id="$(docker image inspect "$GLM53F_IMAGE_REF" \
  --format '{{.Id}}' 2>/dev/null)" ||
  die "pinned image is not staged: $GLM53F_IMAGE_REF"
[[ "$actual_image_id" == "$GLM53F_IMAGE_ID" ]] ||
  die "image ID mismatch: got $actual_image_id, expected $GLM53F_IMAGE_ID"
image_ref="$GLM53F_IMAGE_REF"

for interface in enp1s0f1np1 enP2p1s0f1np1; do
  [[ -d "/sys/class/net/$interface" ]] ||
    die "fabric interface is absent: $interface"
  [[ "$(<"/sys/class/net/$interface/carrier")" == "1" ]] ||
    die "fabric interface has no carrier: $interface"
done

detect_ipv4_gid_index() {
  local hca="$1"
  local suffix="$2"
  local type_path index gid type
  for type_path in /sys/class/infiniband/"$hca"/ports/1/gid_attrs/types/*; do
    index="${type_path##*/}"
    type="$(<"$type_path")"
    gid="$(<"/sys/class/infiniband/$hca/ports/1/gids/$index")"
    if [[ "$type" == *"RoCE v2"* && "$gid" == *"ffff:$suffix" ]]; then
      printf '%s\n' "$index"
      return 0
    fi
  done
  return 1
}

node_octet=$((rank + 1))
printf -v primary_suffix '0a4d:00%02x' "$node_octet"
printf -v secondary_suffix '0a4d:01%02x' "$node_octet"
primary_gid="$(detect_ipv4_gid_index rocep1s0f1 "$primary_suffix")" ||
  die "no IPv4 RoCE-v2 GID for 10.77.0.$node_octet"
secondary_gid="$(detect_ipv4_gid_index roceP2p1s0f1 "$secondary_suffix")" ||
  die "no IPv4 RoCE-v2 GID for 10.77.1.$node_octet"
[[ "$primary_gid" == "$secondary_gid" ]] ||
  die "the two rails require the same GID index but got $primary_gid/$secondary_gid"
gid_index="$primary_gid"

if [[ "${GLM53F_PREFLIGHT_ONLY:-0}" != "1" ]]; then
  docker ps --format '{{.Names}}' | grep -qx "$flagship_container" &&
    die "flagship container is still running"
  docker ps --format '{{.Names}}' |
    grep -qx 'qwen3-vl-30b-pin-gate-v4-vllm' &&
    die "vision is still running"
fi

api_args=()
if [[ "$rank" == "0" ]]; then
  key_file="$HOME/.secrets/glm53-key"
  [[ -s "$key_file" ]] || die "head API key is missing: $key_file"
  api_args=(--api-key "$(<"$key_file")")
fi

if [[ "${GLM53F_PREFLIGHT_ONLY:-0}" == "1" ]]; then
  printf 'preflight passed rank=%s host=%s gid=%s image=%s model=%s\n' \
    "$rank" "$host_ip" "$gid_index" "$GLM53F_IMAGE_DIGEST" \
    "$GLM53F_MODEL_REVISION"
  exit 0
fi

[[ "$GLM53F_DEPLOYMENT_HOLD" == "0" ]] ||
  die "deployment remains on hold"

headless_args=()
[[ "$rank" != "0" ]] && headless_args=(--headless)
default_chat_template_kwargs="{\"reasoning_effort\":\"$reasoning_effort\",\"enable_thinking\":true}"
if [[ "$spec_mode" == "dflash2" ]]; then
  speculative_config="{\"method\":\"dflash\",\"model\":\"$drafter_container_path\",\"num_speculative_tokens\":$dflash_tokens}"
else
  speculative_config="{\"method\":\"mtp\",\"num_speculative_tokens\":$mtp_tokens}"
fi

# FULL_AND_PIECEWISE on this fp8+marlin image. --enforce-eager is only for
# the b12x/NVFP4-KV lane and the topkfix image (those deadlock under graphs).
graph_args=()
if [[ "$cudagraph_mode" == "NONE" ]]; then
  graph_args=(--enforce-eager)
else
  graph_args=(--compilation-config "{\"cudagraph_mode\":\"$cudagraph_mode\"}")
fi

mkdir -p "$cache_host_path"
docker rm -f "$container_name" >/dev/null 2>&1 || true

docker run --gpus all -d \
  --name "$container_name" --restart no \
  --network host --ipc host --shm-size 32g \
  --ulimit memlock=-1:-1 --cap-add IPC_LOCK \
  --device /dev/infiniband:/dev/infiniband \
  --mount "type=bind,src=$model_host_path,dst=$model_path,readonly" \
  "${drafter_mount_args[@]}" \
  --mount "type=bind,src=$cache_host_path,dst=/cache" \
  --mount "type=bind,src=$indexer_host_path,dst=/usr/local/lib/python3.12/dist-packages/vllm/model_executor/layers/sparse_attn_indexer_kpool.py,readonly" \
  --env "VLLM_HOST_IP=$host_ip" \
  --env VLLM_ENGINE_READY_TIMEOUT_S=3600 \
  --env HF_HOME=/cache/huggingface \
  --env HF_HUB_OFFLINE=1 \
  --env TRANSFORMERS_OFFLINE=1 \
  --env VLLM_ALLOW_LONG_MAX_MODEL_LEN=1 \
  --env PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  --env TORCH_CUDA_ARCH_LIST=12.1a \
  --env FLASHINFER_CUDA_ARCH_LIST=12.1a \
  --env FLASHINFER_DISABLE_VERSION_CHECK=1 \
  --env NCCL_NET=IB \
  --env NCCL_IB_DISABLE=0 \
  --env "NCCL_IB_HCA=rocep1s0f1,roceP2p1s0f1" \
  --env "NCCL_IB_GID_INDEX=$gid_index" \
  --env NCCL_IB_ROCE_VERSION_NUM=2 \
  --env NCCL_IB_ADDR_FAMILY=AF_INET \
  --env NCCL_IB_ADDR_RANGE=10.77.0.0/23 \
  --env NCCL_SOCKET_IFNAME=enp1s0f1np1 \
  --env GLOO_SOCKET_IFNAME=enp1s0f1np1 \
  --env TP_SOCKET_IFNAME=enp1s0f1np1 \
  --env NCCL_NVLS_ENABLE=0 \
  --env NCCL_CROSS_NIC=0 \
  --env NCCL_IB_MERGE_NICS=0 \
  --env NCCL_CUMEM_ENABLE=0 \
  --env NCCL_ALGO=Ring \
  --env NCCL_IGNORE_CPU_AFFINITY=1 \
  --env NCCL_MAX_NCHANNELS=4 \
  --env NCCL_MIN_NCHANNELS=4 \
  --env NCCL_DEBUG=WARN \
  --env TORCH_NCCL_ASYNC_ERROR_HANDLING=1 \
  "$image_ref" \
  "$model_path" \
  --served-model-name glm-5.3-flash \
  --host 0.0.0.0 --port "$port" \
  "${api_args[@]}" \
  --trust-remote-code \
  --quantization compressed-tensors \
  --tensor-parallel-size 4 \
  --gpu-memory-utilization 0.85 \
  --max-model-len "$max_model_len" \
  --max-num-seqs "$max_num_seqs" \
  --max-num-batched-tokens "$max_num_batched_tokens" \
  --block-size 2304 \
  --moe-backend marlin \
  --speculative-config "$speculative_config" \
  --kv-cache-dtype fp8_e4m3 \
  --kv-cache-memory "$kv_cache_memory" \
  "${graph_args[@]}" \
  --tool-call-parser glm47 \
  --enable-auto-tool-choice \
  --reasoning-parser glm45 \
  --chat-template "$model_path/chat_template_mm.jinja" \
  --default-chat-template-kwargs "$default_chat_template_kwargs" \
  --distributed-executor-backend mp \
  --nnodes 4 \
  --node-rank "$rank" \
  --master-addr "$head_ip" \
  --master-port "$master_port" \
  "${headless_args[@]}"

sleep 2
docker ps --format '{{.Names}}' | grep -qx "$container_name" ||
  die "$container_name exited immediately; capture logs before removing it"
printf 'launched %s rank=%s host=%s gid=%s\n' \
  "$container_name" "$rank" "$host_ip" "$gid_index"
