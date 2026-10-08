#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'GLM-5.3 shard consolidation: %s\n' "$*" >&2
  exit 1
}

[[ "$(hostname -s)" == "spark-49af" ]] ||
  die "run this script on DGX2 (spark-49af)"

revision="206507bbb047d8223964a0414cd83230c59428f9"
repo="Tech2wild/GLM-5.3-Int4-Int8Mix"
dest="/srv/models/GLM-5.3-Int4-Int8Mix"
venv="$HOME/.venvs/hf-download"
token_file="$HOME/.secrets/hf-token"

[[ -f "$dest/HARNESS_SHARD_SET_set0" ]] ||
  die "head shard set is not complete"
[[ "$(<"$dest/HARNESS_SHARD_SET_set0")" == "$revision" ]] ||
  die "head shard set revision mismatch"
[[ -f "$dest/HARNESS_SHARD_SET_set4" ]] ||
  die "M5 shard set is not complete"
[[ "$(<"$dest/HARNESS_SHARD_SET_set4")" == "$revision" ]] ||
  die "M5 shard set revision mismatch"

workers=(
  "samkimasus1@10.77.0.2:/home/samkimasus1/glm53-model-stage:set1"
  "samkim3@10.77.0.3:/home/samkim3/glm53-model-stage:set2"
  "samkimasus3@10.77.0.4:/home/samkimasus3/glm53-model-stage:set3"
)

for item in "${workers[@]}"; do
  target="${item%%:*}"
  rest="${item#*:}"
  path="${rest%%:*}"
  label="${rest##*:}"
  value="$(
    ssh -o BatchMode=yes "$target" \
      "cat '$path/HARNESS_SHARD_SET_$label' 2>/dev/null"
  )" || die "$label is not complete"
  [[ "$value" == "$revision" ]] ||
    die "$label revision mismatch"
done

pids=()
for item in "${workers[@]}"; do
  target="${item%%:*}"
  rest="${item#*:}"
  path="${rest%%:*}"
  label="${rest##*:}"
  printf 'copying %s from %s\n' "$label" "$target"
  rsync -a --whole-file --partial --info=progress2 \
    "$target:$path/model-*.safetensors" "$dest/" \
    >"$HOME/glm53-tp4/downloads/rsync-$label.log" 2>&1 &
  pids+=("$!")
done
for pid in "${pids[@]}"; do
  wait "$pid" || die "a shard transfer failed"
done

shards=("$dest"/model-*.safetensors)
[[ "${#shards[@]}" == "282" ]] ||
  die "expected 282 model shards, found ${#shards[@]}"

export HF_TOKEN
HF_TOKEN="$(<"$token_file")"
"$venv/bin/hf" cache verify "$repo" \
  --revision "$revision" \
  --local-dir "$dest" \
  --fail-on-missing-files

printf '%s\n' "$revision" >"$dest/HARNESS_PINNED_REVISION"
sync
touch "$dest/HARNESS_DOWNLOAD_VERIFIED"
printf 'verified revision=%s shards=%s bytes=%s\n' \
  "$revision" "${#shards[@]}" "$(du -sb "$dest" | cut -f1)"
