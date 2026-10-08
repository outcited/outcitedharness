#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'GLM-5.3 rank launch: %s\n' "$*" >&2
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

site_env="${GLM53_SITE_ENV:-/mnt/models/deploy/glm53-site.env}"
[[ -f "$site_env" ]] || die "site configuration is missing: $site_env"
launch_profile="${GLM53_LAUNCH_PROFILE:-bare}"
# shellcheck source=/dev/null
source "$site_env"

case "$launch_profile" in
  production)
    [[ "${GLM53_ALLOW_EXPERIMENTAL_MTP:-0}" == "1" ]] ||
      die "MTP profile is experimental; use the qualified bare profile"
    GLM53_SPEC_MODE=mtp
    GLM53_CUDAGRAPH_MODE=NONE
    GLM53_MAX_NUM_SEQS=2
    ;;
  bare)
    GLM53_SPEC_MODE=none
    GLM53_CUDAGRAPH_MODE=NONE
    GLM53_MAX_NUM_SEQS=2
    ;;
  *) die "GLM53_LAUNCH_PROFILE must be production or bare" ;;
esac

: "${GLM53_DEPLOYMENT_HOLD:=1}"
: "${GLM53_IMAGE_REF:?missing GLM53_IMAGE_REF}"
: "${GLM53_IMAGE_ID:?missing GLM53_IMAGE_ID}"
: "${GLM53_MODEL_REVISION:?missing GLM53_MODEL_REVISION}"
: "${GLM53_DCP_RECIPE_REVISION:?missing GLM53_DCP_RECIPE_REVISION}"
: "${GLM53_NCCL_SHA256:?missing GLM53_NCCL_SHA256}"

model_host_path="/mnt/models/GLM-5.3-Int4-Int8Mix"
model_path="/models/glm-5.3"
kernels_host_path="$HOME/glm53-tp4/kernels"
dcp_host_path="$HOME/glm53-tp4/dcp"
nccl_host_path="$HOME/glm53-tp4/runtime/libnccl.so.2"
cache_host_path="$HOME/.cache/glm53-vllm"
container_name="glm53-tp4"
master_port="${GLM53_MASTER_PORT:-29553}"
port="${GLM53_PORT:-8000}"
kv_cache_memory="${GLM53_KV_CACHE_MEMORY:-7200000000}"
max_model_len="${GLM53_MAX_MODEL_LEN:-524288}"
max_num_seqs="${GLM53_MAX_NUM_SEQS:-2}"
max_num_batched_tokens="${GLM53_MAX_NUM_BATCHED_TOKENS:-2048}"
long_prefill_threshold="${GLM53_LONG_PREFILL_THRESHOLD:-2048}"
dcp_size="${GLM53_DCP_SIZE:-4}"
dcp_backend="${GLM53_DCP_BACKEND:-ag_rs}"
dcp_interleave="${GLM53_DCP_INTERLEAVE:-1}"
dcp_compact="${GLM53_DCP_COMPACT:-1}"
dcp_q_pregather="${GLM53_DCP_Q_PREGATHER:-0}"
spec_mode="${GLM53_SPEC_MODE:-mtp}"
mtp_tokens="${GLM53_MTP_TOKENS:-4}"
cudagraph_mode="${GLM53_CUDAGRAPH_MODE:-NONE}"
reasoning_effort="${GLM53_REASONING_EFFORT:-max}"

case "$spec_mode" in
  none) spec_args=() ;;
  mtp)
    spec_args=(
      --speculative-config
      "{\"method\":\"mtp\",\"num_speculative_tokens\":$mtp_tokens,\"draft_tensor_parallel_size\":1,\"attention_backend\":\"FLASHMLA_SPARSE\"}"
    )
    ;;
  *) die "GLM53_SPEC_MODE must be none or mtp" ;;
esac
case "$cudagraph_mode" in
  NONE|FULL) ;;
  *) die "GLM53_CUDAGRAPH_MODE must be NONE or FULL" ;;
esac
[[ "$dcp_size" == "4" ]] || die "this deployment is validated only for DCP4"
[[ "$dcp_backend" == "ag_rs" ]] ||
  die "this deployment requires the ag_rs DCP backend"
[[ "$dcp_interleave" == "1" ]] ||
  die "this deployment requires DCP interleave 1"
[[ "$dcp_compact" =~ ^[01]$ ]] ||
  die "GLM53_DCP_COMPACT must be 0 or 1"
[[ "$dcp_q_pregather" =~ ^[01]$ ]] ||
  die "GLM53_DCP_Q_PREGATHER must be 0 or 1"
case "$reasoning_effort" in
  low|high|max) ;;
  *) die "GLM53_REASONING_EFFORT must be low, high, or max" ;;
esac
compilation_config="{\"cudagraph_mode\":\"$cudagraph_mode\"}"
default_chat_template_kwargs="{\"reasoning_effort\":\"$reasoning_effort\",\"enable_thinking\":true}"

[[ -f "$model_host_path/config.json" ]] ||
  die "model config is missing from $model_host_path"
[[ -f "$model_host_path/model.safetensors.index.json" ]] ||
  die "model weight index is missing"
[[ -f "$model_host_path/chat_template.jinja" ]] ||
  die "chat template is missing"
model_shards=("$model_host_path"/model-*.safetensors)
[[ "${#model_shards[@]}" == "282" ]] ||
  die "expected 282 model shards, found ${#model_shards[@]}"
[[ "$(<"$model_host_path/HARNESS_PINNED_REVISION")" == "$GLM53_MODEL_REVISION" ]] ||
  die "model revision does not match the site pin"
[[ -f "$model_host_path/HARNESS_DOWNLOAD_VERIFIED" ]] ||
  die "model download has not passed the verification gate"
[[ -f "$nccl_host_path" ]] ||
  die "NCCL 2.30.4 runtime is missing: $nccl_host_path"
[[ "$(sha256sum "$nccl_host_path" | cut -d ' ' -f 1)" == "$GLM53_NCCL_SHA256" ]] ||
  die "NCCL 2.30.4 checksum does not match the site pin"
(
  cd "$HOME/glm53-tp4"
  sha256sum --check --status kernel-manifest.sha256
) || die "SM121 kernel overlay checksums do not match the manifest"
(
  cd "$HOME/glm53-tp4"
  sha256sum --check --status dcp-manifest.sha256
) || die "DCP overlay checksums do not match the manifest"

kernel_files=(
  sparse_mla_kernels.py sparse_mla_env.py sm12x_sparse_mla_attn.py
  patch_flashmla_ops.py flashmla_sparse.py sm12x_deep_gemm_fallbacks.py
  sm12x_mqa.py b12x_sparse_helpers.py sparse_attn_indexer.py deepseek_v2.py
)
for kernel in "${kernel_files[@]}"; do
  [[ -f "$kernels_host_path/$kernel" ]] ||
    die "SM121 kernel overlay is missing: $kernels_host_path/$kernel"
done
dcp_files=(
  b12x_sparse_helpers.py block_table.py cp_utils.py flash_attn.py
  flashmla_sparse.py gpu_input_batch.py gpu_model_runner.py indexer.py
  kv_cache_coordinator.py kv_cache_interface.py kv_cache_utils.py
  mla_attention.py scheduler.py sparse_attn_indexer.py sparse_utils.py
  structured_output_init.py structured_output_request.py
)
for overlay in "${dcp_files[@]}"; do
  [[ -f "$dcp_host_path/$overlay" ]] ||
    die "DCP overlay is missing: $dcp_host_path/$overlay"
done
grep -q "GlmMoeDsaForCausalLM" "$kernels_host_path/deepseek_v2.py" ||
  die "deepseek_v2.py does not support the flagship architecture"
if grep -q "fused_indexer_q_rope_quant" "$kernels_host_path/deepseek_v2.py" &&
  ! grep -Eq "def[[:space:]]+fused_indexer_q_rope_quant" \
    "$dcp_host_path/sparse_attn_indexer.py"; then
  die "deepseek_v2.py and sparse_attn_indexer.py are version-skewed"
fi
grep -q "triton_filter_and_convert_dcp_index" "$dcp_host_path/sparse_utils.py" ||
  die "sparse_utils.py is not the DCP overlay"
grep -q "_merge_dcp_topk_global" "$dcp_host_path/sparse_attn_indexer.py" ||
  die "sparse_attn_indexer.py is not the DCP overlay"
if ! grep -q "def _dcp_localize_decode_seq_lens" "$dcp_host_path/indexer.py" ||
  ! grep -q "use_native and next_n > 1" "$dcp_host_path/indexer.py" ||
  ! grep -q "or current_platform.is_device_capability_family(120)" \
    "$dcp_host_path/indexer.py"; then
  die "indexer.py lacks native-MTP DCP localization"
fi
if ! grep -q "new_token_ids=new_token_ids" "$dcp_host_path/scheduler.py" ||
  ! grep -q "def trim_reasoning_for_advance" \
    "$dcp_host_path/structured_output_init.py" ||
  ! grep -q "reasoning_end_token_index" \
    "$dcp_host_path/structured_output_request.py"; then
  die "structured-output MTP reasoning-boundary backport is incomplete"
fi
grep -q "Step3p5MTPProposer" "$dcp_host_path/gpu_model_runner.py" ||
  die "gpu_model_runner.py lacks native-MTP support"
grep -q "cp_world_size_for_kv_cache_spec" "$dcp_host_path/kv_cache_interface.py" ||
  die "kv_cache_interface.py lacks per-group DCP sizing"

actual_image_id="$(docker image inspect "$GLM53_IMAGE_REF" --format '{{.Id}}' 2>/dev/null)" ||
  die "pinned image is not staged: $GLM53_IMAGE_REF"
[[ "$actual_image_id" == "$GLM53_IMAGE_ID" ]] ||
  die "image ID mismatch: got $actual_image_id, expected $GLM53_IMAGE_ID"

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
  die "the two rails require different GID indexes ($primary_gid/$secondary_gid)"
gid_index="$primary_gid"

if [[ "${GLM53_PREFLIGHT_ONLY:-0}" != "1" ]] &&
  docker ps --format '{{.Names}}' |
    grep -qx 'qwen3-vl-30b-pin-gate-v4-vllm'; then
  die "vision is still running; drain it before launching GLM"
fi

api_args=()
if [[ "$rank" == "0" ]]; then
  key_file="$HOME/.secrets/glm53-key"
  [[ -s "$key_file" ]] || die "head API key is missing: $key_file"
  api_args=(--api-key "$(<"$key_file")")
fi

if [[ "${GLM53_PREFLIGHT_ONLY:-0}" == "1" ]]; then
  printf 'preflight passed rank=%s host=%s gid=%s image=%s model=%s dcp=%s\n' \
    "$rank" "$host_ip" "$gid_index" "$actual_image_id" \
    "$GLM53_MODEL_REVISION" "$GLM53_DCP_RECIPE_REVISION"
  exit 0
fi

[[ "$GLM53_DEPLOYMENT_HOLD" == "0" ]] ||
  die "deployment is on hold pending long-context validation"

headless_args=()
[[ "$rank" != "0" ]] && headless_args=(--headless)

mkdir -p "$cache_host_path"
docker rm -f "$container_name" >/dev/null 2>&1 || true

mla_dir="/usr/local/lib/python3.12/dist-packages/vllm/v1/attention/backends/mla"
ops_dir="/usr/local/lib/python3.12/dist-packages/vllm/v1/attention/ops/deepseek_v4_ops"
layers_dir="/usr/local/lib/python3.12/dist-packages/vllm/model_executor/layers"
models_dir="/usr/local/lib/python3.12/dist-packages/vllm/model_executor/models"
vllm_dir="/usr/local/lib/python3.12/dist-packages/vllm"
structured_output_dir="$vllm_dir/v1/structured_output"

docker run --gpus all -d \
  --name "$container_name" --restart no \
  --network host --ipc host --shm-size 10g \
  --ulimit memlock=-1:-1 --cap-add IPC_LOCK \
  --device /dev/infiniband:/dev/infiniband \
  --mount "type=bind,src=$model_host_path,dst=$model_path,readonly" \
  --mount "type=bind,src=$cache_host_path,dst=/cache" \
  --mount "type=bind,src=$nccl_host_path,dst=/opt/harness/libnccl.so.2,readonly" \
  --mount "type=bind,src=$kernels_host_path/sparse_mla_kernels.py,dst=$mla_dir/sparse_mla_kernels.py,readonly" \
  --mount "type=bind,src=$kernels_host_path/sparse_mla_env.py,dst=$mla_dir/sparse_mla_env.py,readonly" \
  --mount "type=bind,src=$kernels_host_path/sm12x_sparse_mla_attn.py,dst=$mla_dir/sm12x_sparse_mla_attn.py,readonly" \
  --mount "type=bind,src=$kernels_host_path/patch_flashmla_ops.py,dst=$mla_dir/patch_flashmla_ops.py,readonly" \
  --mount "type=bind,src=$kernels_host_path/sm12x_deep_gemm_fallbacks.py,dst=$ops_dir/sm12x_deep_gemm_fallbacks.py,readonly" \
  --mount "type=bind,src=$kernels_host_path/sm12x_mqa.py,dst=$ops_dir/sm12x_mqa.py,readonly" \
  --mount "type=bind,src=$kernels_host_path/deepseek_v2.py,dst=$models_dir/deepseek_v2.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/b12x_sparse_helpers.py,dst=$ops_dir/b12x_sparse_helpers.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/flashmla_sparse.py,dst=$mla_dir/flashmla_sparse.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/sparse_utils.py,dst=$mla_dir/sparse_utils.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/indexer.py,dst=$mla_dir/indexer.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/sparse_attn_indexer.py,dst=$layers_dir/sparse_attn_indexer.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/mla_attention.py,dst=$layers_dir/attention/mla_attention.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/kv_cache_interface.py,dst=$vllm_dir/v1/kv_cache_interface.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/kv_cache_utils.py,dst=$vllm_dir/v1/core/kv_cache_utils.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/kv_cache_coordinator.py,dst=$vllm_dir/v1/core/kv_cache_coordinator.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/block_table.py,dst=$vllm_dir/v1/worker/block_table.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/gpu_input_batch.py,dst=$vllm_dir/v1/worker/gpu_input_batch.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/gpu_model_runner.py,dst=$vllm_dir/v1/worker/gpu_model_runner.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/cp_utils.py,dst=$vllm_dir/v1/worker/cp_utils.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/flash_attn.py,dst=$vllm_dir/v1/attention/backends/flash_attn.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/scheduler.py,dst=$vllm_dir/v1/core/sched/scheduler.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/structured_output_init.py,dst=$structured_output_dir/__init__.py,readonly" \
  --mount "type=bind,src=$dcp_host_path/structured_output_request.py,dst=$structured_output_dir/request.py,readonly" \
  --env "VLLM_HOST_IP=$host_ip" \
  --env "NODE_RANK=$rank" \
  --env "MASTER_ADDR=$head_ip" \
  --env VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=1800 \
  --env VLLM_ENGINE_READY_TIMEOUT_S=3600 \
  --env HF_HOME=/cache/huggingface \
  --env TRITON_CACHE_DIR=/cache/triton \
  --env HF_HUB_OFFLINE=1 \
  --env TRANSFORMERS_OFFLINE=1 \
  --env VLLM_ALLOW_LONG_MAX_MODEL_LEN=1 \
  --env VLLM_SPARSE_INDEXER_MAX_LOGITS_MB=256 \
  --env "GLM_DCP_COMPACT=$dcp_compact" \
  --env "GLM_DCP_Q_PREGATHER=$dcp_q_pregather" \
  --env PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  --env CUDA_DEVICE_MAX_CONNECTIONS=32 \
  --env CUTE_DSL_ARCH=sm_121a \
  --env TORCH_CUDA_ARCH_LIST=12.1a \
  --env GLM52_BIND_HOST_TRITON=1 \
  --env GLM52_MQA_LOGITS_TRITON=1 \
  --env GLM52_PAGED_MQA_TRITON=1 \
  --env GLM52_PAGED_MQA_TOPK_CHUNK_SIZE=8192 \
  --env GLM52_B12X_MLA=1 \
  --env VLLM_DISABLE_FLASHINFER_AUTOTUNE=1 \
  --env VLLM_MARLIN_USE_ATOMIC_ADD=1 \
  --env VLLM_FLASHINFER_DECODE_USE_TENSOR_CORES=1 \
  --env OMP_NUM_THREADS=8 \
  --env LD_PRELOAD=/opt/harness/libnccl.so.2 \
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
  --env NCCL_DEBUG=INFO \
  --env TORCH_NCCL_ASYNC_ERROR_HANDLING=1 \
  "$GLM53_IMAGE_REF" \
  vllm serve "$model_path" \
  --served-model-name glm-5.3 \
  --host 0.0.0.0 --port "$port" \
  "${api_args[@]}" \
  --trust-remote-code \
  --quantization compressed-tensors \
  --enable-prefix-caching \
  --async-scheduling \
  --tensor-parallel-size 4 \
  --pipeline-parallel-size 1 \
  --decode-context-parallel-size "$dcp_size" \
  --cp-kv-cache-interleave-size "$dcp_interleave" \
  --dcp-comm-backend "$dcp_backend" \
  --gpu-memory-utilization 0.91 \
  --max-model-len "$max_model_len" \
  --max-num-seqs "$max_num_seqs" \
  --max-num-batched-tokens "$max_num_batched_tokens" \
  --long-prefill-token-threshold "$long_prefill_threshold" \
  "${spec_args[@]}" \
  --kv-cache-dtype fp8_ds_mla \
  --kv-cache-memory-bytes "$kv_cache_memory" \
  --tool-call-parser glm47 \
  --enable-auto-tool-choice \
  --reasoning-parser glm45 \
  --chat-template "$model_path/chat_template.jinja" \
  --default-chat-template-kwargs "$default_chat_template_kwargs" \
  --distributed-executor-backend mp \
  --compilation-config "$compilation_config" \
  --nnodes 4 \
  --node-rank "$rank" \
  --master-addr "$head_ip" \
  --master-port "$master_port" \
  "${headless_args[@]}"

sleep 2
docker ps --format '{{.Names}}' | grep -qx "$container_name" ||
  die "$container_name exited immediately; capture its logs before removing it"
printf 'launched %s rank=%s host=%s gid=%s\n' \
  "$container_name" "$rank" "$host_ip" "$gid_index"
