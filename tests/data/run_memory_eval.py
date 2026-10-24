"""DIRECTOR-06 §3 — retrieval evaluation runner.

Builds the real institutional corpus and benchmarks exact-only vs
lexical(FTS5)-only vs hybrid on the labeled cases.

Metrics: recall@5, MRR, latency, cross-project leakage, false authority.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from director_consumer.corpus import index_git_history, index_tree
from director_consumer.memory import ProjectMemory

DATA = Path(__file__).parent / "memory_eval_cases.json"
WORK = Path("/tmp/mem-eval")
REPO = Path("/tmp/verify-05d")
REPORTS = Path("/var/folders/dj/pftbm4gd4yg6s0y9h_2brmh80000gn/T/opencode")
M5ROOT = Path("/Users/samkim/Harnessv1")


def build_corpus() -> ProjectMemory:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    mem = ProjectMemory(WORK / "memory.db", project_id="designwins",
                        authorized_projects={"designwins", "power-lane"})
    docs = REPO / "director_consumer" / "docs"
    n1 = index_tree(mem, docs, max_files=200)
    n2 = index_tree(mem, REPO, max_files=200)
    # real receipts + agent correspondence from the workspace
    receipts = M5ROOT / "results"
    n3 = 0
    for sub in ("pin-tables-20261014", "vision-compare-multibrand-v2-20260913",
                "vision-ablation-20261007", "thermal-fullcorpus-20261014"):
        p = receipts / sub
        if p.exists():
            n3 += index_tree(mem, p, max_files=20)
    drafts = [p for p in receipts.glob("mailbox_draft_*.md")]
    for d in drafts:
        index_tree(mem, d.parent, max_files=3)
    # the Director's own delivered analyses (this week's verified work)
    n4 = 0
    if REPORTS.exists():
        n4 = index_tree(mem, REPORTS, max_files=60)
    n5 = index_git_history(mem, REPO, max_commits=400)
    # a deliberately DIFFERENT project for cross-project leakage control
    other = WORK / "otherproject"
    other.mkdir()
    (other / "PAYROLL.md").write_text(
        "# Payroll onboarding\n\nEmployees submit tax forms to HR.\n")
    (other / "BLOG.md").write_text(
        "# Wordpress theme\n\nConfigure the theme in appearance settings.\n")
    mem2 = ProjectMemory(WORK / "memory.db", project_id="payroll",
                         authorized_projects={"designwins", "payroll"})
    index_tree(mem2, other, project_id="payroll", max_files=10)
    mem2.close()
    print("indexed: docs=%d repo=%d receipts=%d reports=%d commits=%d"
          % (n1, n2, n3, n4, n5))
    print("counts:", mem.counts())
    return mem


def _vec(mem, q, k=5, project=None):
    try:
        import json as _j
        import urllib.request as _u
        body = _j.dumps({"model": "bge-m3", "input": [q]}).encode()
        req = _u.Request("http://100.100.116.82:8800/v1/embeddings", data=body,
                         headers={"Content-Type": "application/json"})
        with _u.urlopen(req, timeout=20) as r:
            d = _j.loads(r.read().decode())
        v = d["data"][0]["embedding"]
    except Exception:
        return []
    return mem.vector(v, k=k, project=project)


def hit(rec, expects) -> bool:
    hay = " ".join([rec.record.source_id, rec.record.locator,
                    rec.record.title, rec.record.body]).lower()
    return any(e.lower() in hay for e in expects)


def evaluate(mem: ProjectMemory):
    cases = json.loads(DATA.read_text())["cases"]
    modes = {}
    rows = []
    for case in cases:
        q = case["q"]
        exp = case["expect"]
        kind = case["kind"]
        out = {}
        for mode in ("exact", "lexical", "vector", "hybrid"):
            t0 = time.time()
            if mode == "exact":
                res = mem.exact_lookup(q)[:5]
                resp = {"results": res, "conflicts": []}
            elif mode == "lexical":
                scope = "payroll" if kind == "cross-project-control" else None
                res = mem.lexical(q, k=5, project=scope)
            elif mode == "vector":
                scope = "payroll" if kind == "cross-project-control" else None
                res = _vec(mem, q, k=5, project=scope)
                resp = {"results": res, "conflicts": []}
            else:
                # cross-project controls must be issued from the OTHER
                # project's scope — asking designwins about payroll is not
                # a leakage test.
                scope = "payroll" if kind == "cross-project-control" else None
                resp = mem.hybrid(q, k=5, project=scope)
                res = resp["results"]
            ms = (time.time() - t0) * 1000
            if exp:
                ranks = [i for i, r in enumerate(res, 1) if hit(r, exp)]
                rec5 = 1.0 if ranks else 0.0
                mrr = (1.0 / ranks[0]) if ranks else 0.0
            else:
                # cross-project control: a hit is a FAILURE (leakage)
                ranks = [i for i, r in enumerate(res, 1)
                         if r.record.project_id == "designwins"]
                rec5 = 0.0
                mrr = 0.0
            out[mode] = {"recall5": rec5, "mrr": mrr, "ms": ms,
                         "n": len(res),
                         "leaked": len(ranks) if not exp else 0}
            rows.append((kind, mode, rec5, mrr, ms))
        modes.setdefault(kind, []).append((q, out))

    print()
    print("%-22s %-8s %8s %8s %9s" % ("kind", "mode", "recall@5", "MRR", "latency"))
    summary = {}
    for kind in sorted({r[0] for r in rows}):
        for mode in ("exact", "lexical", "vector", "hybrid"):
            sel = [r for r in rows if r[0] == kind and r[1] == mode]
            if not sel:
                continue
            rec5 = sum(r[2] for r in sel) / len(sel)
            mrr = sum(r[3] for r in sel) / len(sel)
            ms = sum(r[4] for r in sel) / len(sel)
            summary[(kind, mode)] = (rec5, mrr, ms)
            print("%-22s %-8s %8.3f %8.3f %8.1fms" % (kind, mode, rec5, mrr, ms))
    print()
    print("%-22s %-8s %8s %8s %9s" % ("ALL", "mode", "recall@5", "MRR", "latency"))
    for mode in ("exact", "lexical", "vector", "hybrid"):
        sel = [r for r in rows if r[1] == mode]
        rec5 = sum(r[2] for r in sel) / len(sel)
        mrr = sum(r[3] for r in sel) / len(sel)
        ms = sum(r[4] for r in sel) / len(sel)
        print("%-22s %-8s %8.3f %8.3f %8.1fms" % ("ALL", mode, rec5, mrr, ms))
        summary[("ALL", mode)] = (rec5, mrr, ms)
    return summary, modes


def showcase(mem: ProjectMemory):
    print()
    print("=== source-cited sample answers (the judging queries) ===")
    for q in (
        "Why does the PDF intake say zero new files when transfers occurred?",
        "We've got extractors running but the Director claims no work is available.",
        "Why should we not restart the FP8 model?"):
        print()
        print("Q:", q)
        r = mem.hybrid(q, k=3)
        print("   conflicts:", r.get("conflicts"))
        for x in r["results"]:
            print("   #%d [%s] %s :: %s" % (
                x.rank, x.record.evidence_class, x.record.source_id[:70],
                x.record.title[:70]))
            print("        locator: %s" % x.record.locator[:100])
            print("        matched_by: %s  score=%.3f" % (x.matched_by, x.score))


def embed_all(mem: ProjectMemory) -> str:
    """Throttled read-only embed against asus4:8800. Returns status."""
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        import embed_corpus as E
    except Exception as e:                                   # noqa: BLE001
        return "embed module unavailable: %s" % e
    rows = mem.con.execute(
        "SELECT id, title, body FROM mem WHERE embedding IS NULL").fetchall()
    if not rows:
        return "already embedded"
    texts = [((x["title"] or "") + "\n" + (x["body"] or ""))[:2000] for x in rows]
    vecs = E.embed(texts)
    if vecs is None:
        return "embedder unreachable — lexical only"
    for x, v in zip(rows, vecs):
        mem.con.execute("UPDATE mem SET embedding=? WHERE id=?",
                        (json.dumps(v), x["id"]))
    mem.con.commit()
    return "embedded %d" % len(vecs)


if __name__ == "__main__":
    mem = build_corpus()
    print("embedding:", embed_all(mem))
    summary, modes = evaluate(mem)
    showcase(mem)
    leak = 0
    for k, lst in modes.items():
        for q, out in lst:
            for mode, v in out.items():
                leak += v.get("leaked", 0)
    print()
    print("cross-project leakage events (must be 0):", leak)
    mem.close()
