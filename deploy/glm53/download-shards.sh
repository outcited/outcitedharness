#!/usr/bin/env bash
set -euo pipefail

label="${1:?usage: download-shards.sh LABEL DEST INCLUDE_PATTERN...}"
dest="${2:?usage: download-shards.sh LABEL DEST INCLUDE_PATTERN...}"
shift 2
(( $# > 0 )) || {
  printf 'at least one include pattern is required\n' >&2
  exit 2
}

repo="Tech2wild/GLM-5.3-Int4-Int8Mix"
revision="206507bbb047d8223964a0414cd83230c59428f9"
venv="$HOME/.venvs/hf-download"
token_file="$HOME/.secrets/hf-token"
log_dir="$HOME/glm53-tp4/downloads"

[[ -x "$venv/bin/hf" ]] || {
  printf 'Hugging Face CLI is missing from %s\n' "$venv" >&2
  exit 1
}
[[ -s "$token_file" ]] || {
  printf 'Hugging Face token file is missing\n' >&2
  exit 1
}

export HF_TOKEN
HF_TOKEN="$(<"$token_file")"
export HF_HOME="$HOME/.cache/huggingface/glm53-$label"
export HF_XET_HIGH_PERFORMANCE=1
export HF_HUB_ETAG_TIMEOUT=60
export HF_HUB_DOWNLOAD_TIMEOUT=600

install -d -m 755 "$dest" "$HF_HOME" "$log_dir"
rm -f "$dest/HARNESS_SHARD_SET_$label"

args=()
for pattern in "$@"; do
  args+=(--include "$pattern")
done

"$venv/bin/hf" download "$repo" \
  --revision "$revision" \
  --local-dir "$dest" \
  "${args[@]}"

printf '%s\n' "$revision" >"$dest/HARNESS_SHARD_SET_$label"
printf 'completed label=%s files=%s bytes=%s\n' \
  "$label" \
  "$(find "$dest" -maxdepth 1 -name 'model-*.safetensors' | wc -l)" \
  "$(du -sb "$dest" | cut -f1)"
