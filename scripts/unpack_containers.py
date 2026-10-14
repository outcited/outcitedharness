#!/usr/bin/env python3
"""S2 — unpack the 1,879 container blobs with parent-child lineage.

PLAN-unseen-doc-types-20261012 §6 S2. The containers (ref-design bundles,
measured: ~11.5 children/zip — gerbers, drill, schematics, netlists, PDFs)
are content-addressed blobs in `corpus-deliveries`. The value is inside;
provenance must survive unpacking (C13: every child carries its own sha and
its parent's).

Law in force:
  * containers are NEVER modified (L0 verbatim)
  * zip-slip: entries with absolute paths or `..` are rejected, recorded,
    and never written outside the container's own child dir
  * zip-bomb: entry-count and total-uncompressed caps, fail-closed — an
    over-limit container is quarantined whole, nothing partial
  * every child is sha256'd before it lands; the class comes from the
    extension layer (`extension_class`), else `unknown` — never guessed

Output:
  results/containers-20261012/lineage.jsonl   one row per accepted child
  results/containers-20261012/summary.json    counts + quarantines + backlog
  /Volumes/M5_4TB/extract-results/containers-20261012/children/<container_sha>/...

Usage: python3 scripts/unpack_containers.py
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from collections import Counter

from harness.electronics.l0 import extension_class

RECLASS = "/Users/samkim/Harnessv1/results/l0-reclass-20261012/l0-records.jsonl"
OUT = "/Users/samkim/Harnessv1/results/containers-20261012"
# /Volumes/M5_4TB is the charley NFS **read-only** lane (transport law: writes
# go elsewhere) — children stage locally. Byte-loss is impossible: the
# container blobs are immutable and content-addressed; any child re-extracts.
CHILDREN_ROOT = "/Users/samkim/Harnessv1/results/containers-20261012/children"

MAX_ENTRIES = 2_000
MAX_TOTAL_BYTES = 2 * 1024**3  # 2 GiB uncompressed per container
MAX_DEPTH = 2  # nested containers unpack at most two levels below the top


def safe_entry_name(name: str) -> str | None:
    """Zip-slip guard: return the normalized relative name, or None if hostile."""
    if not name or name.endswith("/"):
        return None  # directory markers are structure, not children
    norm = name.replace("\\", "/")
    if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
        return None  # absolute (posix or drive)
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts) if parts else None


def unpack_one(container_sha: str, path: str, batch: str, lineage_fh) -> dict:
    result = {"container_sha": container_sha, "batch": batch, "children": 0,
              "rejected_entries": 0, "quarantine": None, "nested": []}
    dest_root = os.path.join(CHILDREN_ROOT, container_sha)
    try:
        zf = zipfile.ZipFile(path)
    except Exception as err:
        result["quarantine"] = f"unreadable: {err}"
        return result

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            result["quarantine"] = f"entry-count bomb: {len(infos)} > {MAX_ENTRIES}"
            return result
        total = 0
        for info in infos:
            total += int(info.file_size)
        if total > MAX_TOTAL_BYTES:
            result["quarantine"] = f"uncompressed bomb: {total} > {MAX_TOTAL_BYTES}"
            return result

        for info in infos:
            name = safe_entry_name(info.filename)
            if name is None:
                if not info.filename.endswith("/"):
                    result["rejected_entries"] += 1
                    lineage_fh.write(json.dumps({
                        "container_sha": container_sha, "container_path": path,
                        "batch": batch, "child_name": info.filename,
                        "status": "rejected_entry",
                        "why": "zip-slip guard (absolute or .. path)",
                    }, ensure_ascii=False) + "\n")
                continue
            try:
                data = zf.read(info)
            except Exception as err:
                result["rejected_entries"] += 1
                lineage_fh.write(json.dumps({
                    "container_sha": container_sha, "container_path": path,
                    "batch": batch, "child_name": info.filename,
                    "status": "unreadable_entry", "why": str(err),
                }, ensure_ascii=False) + "\n")
                continue

            child_sha = hashlib.sha256(data).hexdigest()
            leaf = os.path.basename(name)
            ext_class = extension_class(leaf)
            if ext_class is not None:
                child_class = ext_class
            elif leaf.lower().endswith(".pdf"):
                child_class = "document_pdf"  # re-entry backlog, not unknown
            else:
                child_class = "unknown"  # fail closed
            dest = os.path.join(dest_root, name)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(data)

            lineage_fh.write(json.dumps({
                "container_sha": container_sha, "container_path": path,
                "batch": batch, "child_name": name, "child_sha": child_sha,
                "child_bytes": len(data), "child_class": child_class,
                "extracted_path": dest, "status": "ok",
            }, ensure_ascii=False) + "\n")
            result["children"] += 1
            if child_class == "container":
                result["nested"].append((child_sha, dest))

    return result


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(CHILDREN_ROOT, exist_ok=True)
    classes: Counter[str] = Counter()
    batches: Counter[str] = Counter()
    containers = 0
    children = 0
    rejected = 0
    quarantined: list[dict] = []

    with open(RECLASS) as fh, open(os.path.join(OUT, "lineage.jsonl"), "w") as lineage_fh:
        work: list[tuple[str, str, str, int]] = []
        for line in fh:
            rec = json.loads(line)
            if rec.get("doc_class") != "container":
                continue
            path = rec.get("source_path", "")
            sha = os.path.splitext(os.path.basename(path))[0]
            batch = rec.get("source_path", "").split("/blobs/")[0].rsplit("/", 1)[-1]
            work.append((sha, path, batch, 0))

        while work:
            sha, path, batch, depth = work.pop()
            containers += 1
            batches[batch] += 1
            result = unpack_one(sha, path, batch, lineage_fh)
            children += result["children"]
            rejected += result["rejected_entries"]
            if result["quarantine"]:
                quarantined.append({"container_sha": sha, "path": path,
                                    "why": result["quarantine"]})
            if depth < MAX_DEPTH:
                for child_sha, child_path in result["nested"]:
                    work.append((child_sha, child_path, f"nested:{batch}", depth + 1))
            elif result["nested"]:
                for child_sha, child_path in result["nested"]:
                    lineage_fh.write(json.dumps({
                        "container_sha": child_sha, "container_path": child_path,
                        "batch": batch, "child_name": os.path.basename(child_path),
                        "status": "depth_capped",
                        "why": f"nested container beyond MAX_DEPTH={MAX_DEPTH}",
                    }, ensure_ascii=False) + "\n")

    # class distribution over accepted children
    with open(os.path.join(OUT, "lineage.jsonl")) as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("status") == "ok":
                classes[row["child_class"]] += 1

    summary = {
        "sweep": "containers-20261012",
        "law": "containers immutable; zip-slip rejected; bomb caps fail-closed; every child sha256'd; class = extension layer or unknown",
        "containers": containers,
        "children_accepted": children,
        "entries_rejected": rejected,
        "containers_quarantined": len(quarantined),
        "quarantine": quarantined[:50],
        "child_classes": dict(classes.most_common()),
        "by_batch": dict(batches.most_common()),
        "backlog_child_pdfs": classes.get("document_pdf", 0),
    }
    with open(os.path.join(OUT, "summary.json"), "w") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print(f"containers: {containers}  children: {children}  rejected: {rejected}  "
          f"quarantined: {len(quarantined)}")
    for k, v in classes.most_common(12):
        print(f"  {k:14s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
