#!/usr/bin/env bash
set -euo pipefail

label="${1:?usage: download-range.sh LABEL DEST FIRST LAST}"
dest="${2:?usage: download-range.sh LABEL DEST FIRST LAST}"
first="${3:?usage: download-range.sh LABEL DEST FIRST LAST}"
last="${4:?usage: download-range.sh LABEL DEST FIRST LAST}"

repo="Tech2wild/GLM-5.3-Int4-Int8Mix"
revision="206507bbb047d8223964a0414cd83230c59428f9"
venv="$HOME/.venvs/hf-download"
token_file="$HOME/.secrets/hf-token"
state_dir="$HOME/glm53-tp4/downloads"

[[ "$first" =~ ^[0-9]+$ && "$last" =~ ^[0-9]+$ ]] ||
  { printf 'FIRST and LAST must be integers\n' >&2; exit 2; }
((first >= 1 && last <= 282 && first <= last)) ||
  { printf 'range must be within 1..282\n' >&2; exit 2; }
[[ -x "$venv/bin/hf" ]] ||
  { printf 'Hugging Face CLI is missing\n' >&2; exit 1; }
[[ -s "$token_file" ]] ||
  { printf 'Hugging Face token file is missing\n' >&2; exit 1; }

export HF_TOKEN
HF_TOKEN="$(<"$token_file")"
export HF_HOME="$HOME/.cache/huggingface/glm53-$label"
unset HF_XET_HIGH_PERFORMANCE HF_XET_HP
export HF_XET_AC_MAX_DOWNLOAD_CONCURRENCY=4
export HF_XET_CHUNK_CACHE_SIZE_BYTES=0
export HF_HUB_ETAG_TIMEOUT=60
export HF_HUB_DOWNLOAD_TIMEOUT=600

install -d -m 755 "$dest" "$HF_HOME" "$state_dir"
exec 9>"$state_dir/$label.lock"
flock -n 9 ||
  { printf '%s already has an active downloader\n' "$label" >&2; exit 1; }
rm -f "$dest/HARNESS_SHARD_SET_$label"

if [[ "$label" == "set0" ]]; then
  metadata=(
    .gitattributes LICENSE README.md chat_template.jinja
    chat_template.jinja.bak-prethinking config.json generation_config.json
    model.safetensors.index.json tokenizer_config.json tokenizer.json
  )
  for file in "${metadata[@]}"; do
    "$venv/bin/hf" download "$repo" "$file" \
      --revision "$revision" --local-dir "$dest"
  done
fi

for ((number = first; number <= last; number++)); do
  printf -v file 'model-%05d-of-00282.safetensors' "$number"
  printf '%s downloading %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$file"
  "$venv/bin/hf" download "$repo" "$file" \
    --revision "$revision" --local-dir "$dest"
  printf '%s %s\n' "$number" "$file" >"$state_dir/$label.status.tmp"
  mv "$state_dir/$label.status.tmp" "$state_dir/$label.status"
done

expected=$((last - first + 1))
actual="$(find "$dest" -maxdepth 1 -name 'model-*.safetensors' | wc -l)"
((actual >= expected)) ||
  { printf 'expected at least %s shards, found %s\n' "$expected" "$actual" >&2; exit 1; }
printf '%s\n' "$revision" >"$dest/HARNESS_SHARD_SET_$label"
printf 'completed label=%s range=%s-%s files=%s bytes=%s\n' \
  "$label" "$first" "$last" "$actual" "$(du -sb "$dest" | cut -f1)"
