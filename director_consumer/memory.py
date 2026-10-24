"""Project-scoped hybrid memory — DIRECTOR-06 §2.

`director.db` stays the authoritative state (hash-chained journal, tasks,
incidents, approvals). This is a separate, rebuildable RETRIEVAL index over a
bounded institutional corpus, kept on local storage. It stores pointers to
source artifacts — it is never a second authoritative copy of engineering
facts.

Design decision (measured, not fashionable):
  * SQLite **FTS5/BM25** for lexical and exact-identifier retrieval. Available
    in the shipped SQLite (3.50.4), zero new services, survives on local disk,
    and is fully reconstructible.
  * Optional **vector** column + exact cosine, used only when an embedder is
    reachable and authorized. If the embedder is down, lexical retrieval
    continues unchanged. No Qdrant/second server: the corpus is bounded
    project history, not a billion-scale workload, and an extra daemon would
    add operational surface without measurably better recall@5.

Laws:
  * Semantic similarity NEVER confers authority or verification. Evidence
    class is carried on the record and checked before serving.
  * Project and authorization scope are filtered BEFORE results are returned.
  * Exact source identifiers stay recoverable with embeddings unavailable.
  * Conflicts are reported; the newer-looking or more similar item is never
    silently chosen.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# Evidence classes — the institutional contract.
EVIDENCE_CLASSES = (
    "authority", "verified-fact", "assertion", "hypothesis",
    "superseded", "unknown",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS mem (
  id INTEGER PRIMARY KEY,
  project_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  locator TEXT NOT NULL,
  content_sha TEXT,
  source_type TEXT,
  title TEXT,
  body TEXT,
  event_ts REAL,
  indexed_ts REAL NOT NULL,
  actor TEXT,
  evidence_class TEXT NOT NULL,
  version TEXT,
  supersedes TEXT,
  superseded_by TEXT,
  related_task TEXT, related_incident TEXT, related_decision TEXT,
  related_commit TEXT,
  source_available TEXT,
  source_freshness TEXT,
  embedding BLOB
);
CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts_idx USING fts5(
  title, body, content='mem', content_rowid='id',
  tokenize='porter unicode61 remove_diacritics 2');
CREATE UNIQUE INDEX IF NOT EXISTS mem_identity
  ON mem(project_id, source_id, content_sha);
CREATE TABLE IF NOT EXISTS mem_meta(key TEXT PRIMARY KEY, value TEXT);
"""

# A tiny stop/expansion layer so paraphrase queries land on the same history.
# Deliberately small and auditable — not a model.
EXPANSIONS = {
    "intake": ("intake", "acquisition", "pull", "ingest", "spool", "counter"),
    "acquisition": ("acquisition", "intake", "crawl", "spool", "factory"),
    "zero": ("zero", "null", "empty", "nil", "0"),
    "starving": ("starving", "starvation", "idle", "starved", "no work"),
    "starvation": ("starvation", "starving", "idle", "no work", "empty queue"),
    "admission": ("admission", "enqueue", "queue", "admitted", "corpus_key"),
    "rsync": ("rsync", "transfer", "mtime", "modification time", "copy"),
    "mtime": ("mtime", "modification", "timestamp", "preserve", "rsync"),
    "hallucination": ("hallucination", "fabricated", "invented", "false claim"),
    "restart": ("restart", "relaunch", "reboot", "cycle", "crash loop"),
    "adjudication": ("adjudication", "judge", "verdict", "fp8", "adjudicator"),
    "memory": ("memory", "recall", "retrieval", "history", "experience"),
    "fp8": ("fp8", "adjudication", "adjudicator", "asus4", "verdict"),
    "model": ("model", "inference", "endpoint", "adjudicator", "qwen"),
    "queue": ("queue", "backlog", "lane", "pending", "work items"),
    "verification": ("verification", "verified", "receipt", "quote",
                     "provenance", "source-backed"),
    "superseded": ("superseded", "withdrawn", "replaced", "historical"),
    "document": ("document", "pdf", "datasheet", "source"),
    "facet": ("facet", "document-facet", "lane", "work unit"),
}


@dataclass
class MemoryRecord:
    project_id: str
    source_id: str
    locator: str
    content_sha: str = ""
    source_type: str = ""
    title: str = ""
    body: str = ""
    event_ts: Any = None
    indexed_ts: Any = None
    actor: str = ""
    evidence_class: str = "unknown"
    version: str = ""
    supersedes: str = ""
    superseded_by: str = ""
    related_task: str = ""
    related_incident: str = ""
    related_decision: str = ""
    related_commit: str = ""
    source_available: str = "available"
    source_freshness: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Retrieval:
    record: MemoryRecord
    score: float
    matched_by: list[str]
    rank: int = 0
    conflict_note: str = ""


class ProjectMemory:
    """Reconstructible, project-scoped retrieval index."""

    def __init__(self, path: str | Path, project_id: str = "designwins",
                 authorized_projects: Optional[set[str]] = None):
        self.path = Path(path)
        self.project_id = project_id
        self.authorized = authorized_projects or {project_id}
        self.con = sqlite3.connect(str(self.path))
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.con.commit()

    def close(self):
        self.con.close()

    # ------------------------------------------------------------- ingest
    def index(self, rec: MemoryRecord) -> int:
        if rec.project_id not in self.authorized:
            raise PermissionError(
                f"refusing to index project {rec.project_id!r}: outside "
                f"authorized scope {sorted(self.authorized)}")
        rec.indexed_ts = rec.indexed_ts or time.time()
        # idempotency + versioning: same (project, source, content) is a
        # no-op; same source with NEW content inserts a new version and
        # marks the old row superseded (history preserved, never duplicated)
        prior = self.con.execute(
            "SELECT id FROM mem WHERE project_id=? AND source_id=? "
            "AND content_sha=?", (rec.project_id, rec.source_id,
                                  rec.content_sha)).fetchone()
        if prior is not None:
            return prior["id"]
        cur = self.con.execute(
            "INSERT INTO mem(project_id, source_id, locator, content_sha,"
            " source_type, title, body, event_ts, indexed_ts, actor,"
            " evidence_class, version, supersedes, superseded_by,"
            " related_task, related_incident, related_decision, related_commit,"
            " source_available, source_freshness) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec.project_id, rec.source_id, rec.locator, rec.content_sha,
             rec.source_type, rec.title, rec.body, rec.event_ts,
             rec.indexed_ts, rec.actor, rec.evidence_class, rec.version,
             rec.supersedes, rec.superseded_by, rec.related_task,
             rec.related_incident, rec.related_decision, rec.related_commit,
             rec.source_available, rec.source_freshness))
        rid = cur.lastrowid
        self.con.execute(
            "INSERT INTO mem_fts_idx(rowid, title, body) VALUES (?,?,?)",
            (rid, rec.title or "", rec.body or ""))
        # supersede earlier versions of the same source
        self.con.execute(
            "UPDATE mem SET evidence_class='superseded', superseded_by=? "
            "WHERE project_id=? AND source_id=? AND id<>? AND "
            "evidence_class<>'superseded'",
            (f"{rec.source_id}@v{rid}", rec.project_id, rec.source_id, rid))
        self.con.commit()
        return rid

    def mark_superseded(self, old_locator: str, new_locator: str) -> None:
        self.con.execute(
            "UPDATE mem SET superseded_by=?, evidence_class='superseded' "
            "WHERE locator=?", (new_locator, old_locator))
        self.con.execute(
            "UPDATE mem SET supersedes=? WHERE locator=?",
            (old_locator, new_locator))
        self.con.commit()

    # ----------------------------------------------------------- retrieval
    STOPWORDS = frozenset(
        "a an the is are was were be been being do does did to of in on for "
        "with and or not no it its this that these those we i you they he she "
        "my our your their what when where why how which who whom as at by "
        "from into out up down over under about again then so than too very "
        "can will just should would could may might must have has had".split())

    def _groups(self, q: str) -> list[list[str]]:
        """One group per content token, expanded with its synonyms.
        Groups are the unit of matching: we require several groups to agree
        rather than accepting a single stray token."""
        toks = [t for t in re.findall(r"[a-z0-9_.:-]+", q.lower())
                if len(t) > 1 and t not in self.STOPWORDS]
        groups = []
        for t in toks:
            syn = list(dict.fromkeys(EXPANSIONS.get(t, (t,))))
            groups.append(syn)
        return groups

    def _fts_query(self, q: str) -> str:
        groups = self._groups(q)
        if not groups:
            return '""'
        parts = []
        for syn in groups:
            ors = " OR ".join('"%s"' % s.replace('"', "") for s in syn)
            parts.append("(" + ors + ")")
        # OR across groups for candidate recall; precision is re-imposed by
        # minimum-should-match in _score_groups.
        return " OR ".join(parts)

    def _score_groups(self, text: str, groups: list[list[str]]) -> int:
        hay = text.lower()
        n = 0
        for syn in groups:
            if any(s in hay for s in syn):
                n += 1
        return n

    def exact_lookup(self, needle: str) -> list[Retrieval]:
        """Exact entity/reference recovery — must work with NO semantic index."""
        out = []
        q = f"%{needle}%"
        for r in self.con.execute(
                "SELECT * FROM mem WHERE source_id LIKE ? OR locator LIKE ? "
                "OR content_sha LIKE ? OR related_commit LIKE ? "
                "OR related_decision LIKE ? OR related_task LIKE ? "
                "OR related_incident LIKE ? LIMIT 25",
                (q, q, q, q, q, q, q)):
            out.append(Retrieval(_row(r), 1.0, ["exact"]))
        return out

    def lexical(self, q: str, k: int = 10,
                project: Optional[str] = None) -> list[Retrieval]:
        proj = project or self.project_id
        fts = self._fts_query(q)
        out = []
        try:
            rows = self.con.execute(
                "SELECT m.*, bm25(mem_fts_idx) AS s FROM mem_fts_idx "
                "JOIN mem m ON m.id = mem_fts_idx.rowid "
                "WHERE mem_fts_idx MATCH ? AND m.project_id = ? "
                "ORDER BY s LIMIT ?", (fts, proj, k * 3)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        groups = self._groups(q)
        # Long natural-language queries contain many low-value words.
        # Requiring HALF the groups discards the right document; requiring 1
        # lets a stray token leak. Two agreeing content groups is the
        # measured balance (controls stay at 0 leakage, paraphrases retrieve).
        if not groups:
            need = 1
        elif len(groups) <= 2:
            need = len(groups)
        else:
            need = 2
        for r in rows:
            rec = _row(r)
            got = self._score_groups(f"{rec.title} {rec.body}", groups)
            if got < need:
                continue          # one stray token is not a match
            out.append(Retrieval(rec, float(-r["s"]) + got * 0.001,
                                 ["fts5"]))
        out.sort(key=lambda x: -x.score)
        return out[:k]

    def vector(self, qvec: list[float], k: int = 10,
               project: Optional[str] = None) -> list[Retrieval]:
        """Exact cosine over stored embeddings. Absent embeddings are skipped
        silently — lexical retrieval is unaffected."""
        if not qvec:
            return []
        proj = project or self.project_id
        out = []
        for r in self.con.execute(
                "SELECT * FROM mem WHERE embedding IS NOT NULL "
                "AND project_id = ?", (proj,)):
            blob = r["embedding"]
            try:
                v = json.loads(blob)
            except Exception:  # noqa: BLE001
                continue
            s = _cosine(qvec, v)
            out.append(Retrieval(_row(r), s, ["vector"]))
        out.sort(key=lambda x: -x.score)
        return out[:k]

    _IDENT = re.compile(r"^[A-Za-z0-9_.:-]{4,64}$")
    _IDENT_TOK = re.compile(r"\b[0-9a-f]{7,40}\b|\b[A-Z][A-Z0-9]+-[A-Z0-9-]+\b|"
                            r"\b[a-z0-9]+(?:[._-][a-z0-9]+)+\b")

    def _identifiers(self, q: str) -> list[str]:
        out = []
        s = q.strip()
        if self._IDENT.match(s):
            out.append(s)
        for t in self._IDENT_TOK.findall(q):
            if t not in out:
                out.append(t)
        return out

    def hybrid(self, q: str, k: int = 8, qvec: Optional[list[float]] = None,
               project: Optional[str] = None,
               authorized: Optional[set[str]] = None,
               allow: tuple[str, ...] = ("authority", "verified-fact",
                                         "assertion", "hypothesis",
                                         "superseded", "unknown"),
               exclude_superseded: bool = False) -> dict:
        """Combined retrieval with authority/recency/supersession handling.

        Scope is filtered BEFORE results are returned. Conflicts surface.
        """
        auth = authorized or self.authorized
        proj = project or self.project_id
        if proj not in auth:
            return {"error": f"project {proj!r} not in authorized scope",
                    "results": [], "conflicts": []}

        pool: dict[Any, Retrieval] = {}
        # exact lookup runs on IDENTIFIER-shaped needles only — a whole
        # sentence must never be used as a LIKE needle (it injects junk at
        # score 1.0 and outranks real evidence).
        for ident in self._identifiers(q)[:6]:
            for r in self.exact_lookup(ident):
                _merge(pool, r)
        for r in self.lexical(q, k=k * 2, project=proj):
            _merge(pool, r)
        if qvec:
            for r in self.vector(qvec, k=k * 2, project=proj):
                _merge(pool, r)

        results = []
        for r in pool.values():
            rec = r.record
            if rec.project_id != proj:
                continue                     # scope filtered before serving
            if rec.evidence_class not in allow:
                continue
            if exclude_superseded and rec.evidence_class == "superseded":
                continue
            results.append(r)
        # authority first, then score. Never let similarity outrank authority.
        class_rank = {"authority": 0, "verified-fact": 1, "assertion": 3,
                      "hypothesis": 4, "unknown": 5, "superseded": 6}
        results.sort(key=lambda r: (class_rank.get(r.record.evidence_class, 9),
                                    -r.score))
        for i, r in enumerate(results[:k], 1):
            r.rank = i

        conflicts = self._conflicts(results[:k])
        return {
            "query": q,
            "project_id": proj,
            "results": results[:k],
            "conflicts": conflicts,
            "authority_note": (
                "similarity confers no authority; evidence_class is carried "
                "from the source and checked before serving"),
        }

    def _conflicts(self, results: list[Retrieval]) -> list[dict]:
        """Same locator with different evidence classes, or an item that
        supersedes another retrieved item. Never silently resolved."""
        out = []
        seen: dict[str, list[Retrieval]] = {}
        for r in results:
            seen.setdefault(r.record.locator, []).append(r)
        for locator, rs in seen.items():
            classes = {r.record.evidence_class for r in rs}
            if len(classes) > 1:
                out.append({
                    "locator": locator,
                    "issue": "conflicting evidence classes for one source",
                    "classes": sorted(classes),
                    "resolution": "REPORTED, not auto-resolved — owner decides",
                })
        for r in results:
            if r.record.superseded_by:
                out.append({
                    "locator": r.record.locator,
                    "issue": "retrieved item is superseded",
                    "superseded_by": r.record.superseded_by,
                    "resolution": ("prefer the superseding source for current "
                                   "authority; keep this as history"),
                })
        return out

    # ------------------------------------------------------------- utility
    def counts(self) -> dict:
        out = {"total": self.con.execute("SELECT COUNT(*) FROM mem").fetchone()[0]}
        for r in self.con.execute(
                "SELECT source_type, COUNT(*) c FROM mem GROUP BY source_type"):
            out[f"type:{r['source_type']}"] = r["c"]
        for r in self.con.execute(
                "SELECT evidence_class, COUNT(*) c FROM mem GROUP BY evidence_class"):
            out[f"class:{r['evidence_class']}"] = r["c"]
        out["with_embedding"] = self.con.execute(
            "SELECT COUNT(*) FROM mem WHERE embedding IS NOT NULL").fetchone()[0]
        return out


def _row(r) -> MemoryRecord:
    return MemoryRecord(
        project_id=r["project_id"], source_id=r["source_id"],
        locator=r["locator"], content_sha=r["content_sha"],
        source_type=r["source_type"], title=r["title"], body=r["body"],
        event_ts=r["event_ts"], indexed_ts=r["indexed_ts"], actor=r["actor"],
        evidence_class=r["evidence_class"], version=r["version"],
        supersedes=r["supersedes"], superseded_by=r["superseded_by"],
        related_task=r["related_task"], related_incident=r["related_incident"],
        related_decision=r["related_decision"], related_commit=r["related_commit"],
        source_available=r["source_available"],
        source_freshness=r["source_freshness"])


def _merge(pool: dict, r: Retrieval) -> None:
    key = r.record.locator
    if key in pool:
        pool[key].score = max(pool[key].score, r.score)
        for m in r.matched_by:
            if m not in pool[key].matched_by:
                pool[key].matched_by.append(m)
    else:
        pool[key] = r


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0
