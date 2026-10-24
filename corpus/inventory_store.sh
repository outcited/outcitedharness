#!/usr/bin/env bash
# usage: inventory_store.sh <cas-root> <out.tsv>  (run ON the box holding the store)
# emits: sha<TAB>size<TAB>path  per file, sha = hex chars of filename
find "$1" -type f 2>/dev/null | while read -r f; do
  b=$(basename "$f"); hex=$(echo "$b" | tr -cd '0123456789abcdefABCDEF')
  [ -n "$hex" ] || continue
  sz=$(stat -c %s "$f" 2>/dev/null || stat -f %z "$f" 2>/dev/null)
  printf '%s\t%s\t%s\n' "$hex" "${sz:-0}" "$f"
done
