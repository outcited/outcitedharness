#!/usr/bin/env bash
set -euo pipefail

dest="${1:-$HOME/Models/GLM-5.3-Int4-Int8Mix-mac-stage}"
repo="Tech2wild/GLM-5.3-Int4-Int8Mix"
revision="206507bbb047d8223964a0414cd83230c59428f9"
venv="$HOME/.venvs/hf-glm53-download"
state_dir="$HOME/.cache/harness/glm53-mac-download"
token_file="${HF_TOKEN_FILE:-$HOME/.secrets/hf-token}"
ranges=(40:69 111:139 181:209 256:282)

[[ -x "$venv/bin/hf" ]] || {
  printf 'Hugging Face CLI is missing: %s\n' "$venv/bin/hf" >&2
  exit 1
}

export HF_HOME="$HOME/.cache/huggingface/glm53-mac"
if [[ -s "$token_file" ]]; then
  export HF_TOKEN
  HF_TOKEN="$(<"$token_file")"
else
  unset HF_TOKEN
fi
unset HF_XET_HIGH_PERFORMANCE HF_XET_HP
export HF_XET_AC_MAX_DOWNLOAD_CONCURRENCY=16
export HF_XET_CHUNK_CACHE_SIZE_BYTES=0
export HF_HUB_ETAG_TIMEOUT=60
export HF_HUB_DOWNLOAD_TIMEOUT=600

install -d -m 755 "$dest" "$HF_HOME" "$state_dir"
rm -f "$dest/HARNESS_SHARD_SET_set4"

expected=0
for range in "${ranges[@]}"; do
  first="${range%:*}"
  last="${range#*:}"
  expected=$((expected + last - first + 1))
  for ((number = first; number <= last; number++)); do
    printf -v file 'model-%05d-of-00282.safetensors' "$number"
    if [[ -s "$dest/$file" ]]; then
      printf '%s present %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$file"
    else
      printf '%s downloading %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$file"
      "$venv/bin/hf" download "$repo" "$file" \
        --revision "$revision" --local-dir "$dest"
    fi
    printf '%s %s\n' "$number" "$file" >"$state_dir/set4.status.tmp"
    mv "$state_dir/set4.status.tmp" "$state_dir/set4.status"
  done
done

actual="$(
  find "$dest" -maxdepth 1 -name 'model-*.safetensors' |
    wc -l |
    tr -d '[:space:]'
)"
[[ "$actual" == "$expected" ]] || {
  printf 'expected %s shards, found %s\n' "$expected" "$actual" >&2
  exit 1
}
printf '%s\n' "$revision" >"$dest/HARNESS_SHARD_SET_set4"
sync
printf 'MAC_RANGE_COMPLETE revision=%s files=%s bytes=%s dest=%s\n' \
  "$revision" "$actual" "$(du -sk "$dest" | awk '{print $1 * 1024}')" "$dest"
