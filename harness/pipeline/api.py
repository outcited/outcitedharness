"""Extraction API v1 — the one front door.

POST /jobs        {pillar, grain, corpus, schema, budget}  -> job id
GET  /jobs/<id>   -> job + latest result (provenance attached)
POST /adjudicate  {result_id} -> synchronous verdicts (reason_code + quote)

Any agent submits; tiers route by budget; provenance rides every claim.
Served by harness.pipeline.api:main (stdlib http.server; no dependencies).
"""

import json
import os
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import store
from .ladder import classify_batch
from .quote_verify import verify_claims

CANON_ENDPOINT = os.environ.get(
    "CANON_ENDPOINT", "http://100.100.116.82:8900/v1/chat/completions"
)
CANON_MODEL = os.environ.get("CANON_MODEL", "qwen3.8-27b-fp8")

ALLOWED_PILLARS = {
    "descriptions", "opns", "pinouts", "balls", "footnotes_conditions",
    "parametric_tables", "absmax_vs_recommended", "package_drawings",
    "reference_manuals", "errata",
}
ALLOWED_GRAINS = {"doc", "page", "row", "node"}
ALLOWED_BUDGETS = {
    "deterministic", "local-student", "local-vision", "local-canon",
    "frontier", "hosted-judge",
}

JUDGE_SYSTEM = (
    "You are a strict datasheet extraction auditor. You receive document text "
    "and a JSON list of claimed rows. For EACH claim verify it against the "
    "text: same value, unit, qualifier, condition. Reply ONLY JSON:"
    ' {"verdicts": [{"i": <index>, "verdict": "SUPPORTED"|"UNSUPPORTED"'
    '|"NOT_IN_DOC", "reason_code": "<class>", "evidence_quote": "<verbatim>'
    ' short quote"}]}. reason_code one of: value_scale, unit_mismatch,'
    " invented_symbol, qualifier_mismatch, condition_missing, absent,"
    " ambiguous. evidence_quote must be verbatim from the text or null."
)


class Handler(BaseHTTPRequestHandler):
    con = None

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        m = re.match(r"^/jobs/(\d+)$", self.path)
        if not m:
            return self._json(404, {"error": "unknown route"})
        row = self.con.execute("SELECT * FROM jobs WHERE id=?", (int(m.group(1)),)).fetchone()
        if not row:
            return self._json(404, {"error": "no such job"})
        res = self.con.execute(
            "SELECT * FROM results WHERE job_id=? ORDER BY created_at DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        return self._json(200, {"job": dict(row), "result": dict(res) if res else None})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        if self.path == "/jobs":
            return self._create_job(req)
        if self.path == "/adjudicate":
            return self._adjudicate(req)
        return self._json(404, {"error": "unknown route"})

    def _create_job(self, req):
        pillar = req.get("pillar")
        grain = req.get("grain", "doc")
        budget = req.get("budget", {}).get("tier", "deterministic")
        schema_name = req.get("schema")
        corpus = req.get("corpus", {})
        corpus_key = corpus.get("selector") or corpus.get("sha256")
        errors = []
        if pillar and pillar not in ALLOWED_PILLARS:
            errors.append(f"pillar '{pillar}' not on the council list")
        if grain not in ALLOWED_GRAINS:
            errors.append(f"grain '{grain}' invalid")
        if budget not in ALLOWED_BUDGETS:
            errors.append(f"budget '{budget}' invalid")
        if not corpus_key:
            errors.append("corpus.selector required")
        if budget == "frontier" and not req.get("quota_ok"):
            errors.append("frontier budget requires explicit quota_ok:true (daily cap enforced)")
        if errors:
            return self._json(422, {"errors": errors})
        ok, spend = store.budget_check(self.con, budget)
        if not ok:
            return self._json(429, {"error": "daily tier cap reached", "spend": spend})
        job_id = store.enqueue(
            self.con, kind="extract", corpus_key=corpus_key,
            source_path=corpus.get("path"), pillar=pillar, grain=grain,
            schema_name=schema_name, budget_tier=budget, payload=req.get("payload"),
        )
        return self._json(201, {"job_id": job_id, "pillar": pillar, "grain": grain,
                                "schema": schema_name, "budget": budget, "spend": spend})

    def _adjudicate(self, req):
        result_id = req.get("result_id")
        row = self.con.execute("SELECT * FROM results WHERE id=?", (result_id,)).fetchone()
        if not row:
            return self._json(404, {"error": "no such result"})
        output = json.loads(row["output"])
        if isinstance(output, list):
            claims = output
            text = req.get("text", "")
        else:
            claims = output.get("claims") or output.get("electrical_bounds", {})
            text = req.get("text") or output.get("_text", "")

        if claims and all(isinstance(c, dict) and "quote" in c for c in
                          (claims.values() if isinstance(claims, dict) else claims)):
            deterministic = verify_claims(claims, text) if isinstance(claims, dict) else []
            if deterministic and all(v["verdict"] == "SUPPORTED" and v["severity"] == "P2"
                                     for v in deterministic):
                store.record_verdicts(self.con, result_id, "quote-verify-deterministic", "1",
                                      [{"claim_index": i, "verdict": v["verdict"],
                                        "reason_code": v["reason_code"], "evidence_quote": v.get("evidence_quote")}
                                       for i, v in enumerate(deterministic)],
                                      extractor=row["extractor"])
                rows, batch = classify_batch([{"verdict": v["verdict"], "reason_code": v["reason_code"]}
                                              for v in deterministic])
                return self._json(200, {"result_id": result_id, "judge": "deterministic-quote-match",
                                        "verdicts": deterministic, "row_actions": rows, "batch": batch})

        try:
            verdicts = call_canon(text, [c for c in (claims.values() if isinstance(claims, dict) else claims)
                                         if not isinstance(c, dict) or c.get("value") is not None])
        except Exception as e:
            return self._json(503, {"error": f"canon unavailable: {e}"})
        for i, v in enumerate(verdicts):
            v.setdefault("claim_index", v.pop("i", i))
        store.record_verdicts(self.con, result_id, CANON_MODEL, "fp8-local", verdicts,
                              extractor=row["extractor"])
        rows, batch = classify_batch(verdicts)
        return self._json(200, {
            "result_id": result_id,
            "judge": CANON_MODEL,
            "verdicts": verdicts,
            "row_actions": rows,
            "batch": batch,
        })


_BREAKER = {"fails": 0, "open_until": 0.0}


def call_canon(text, claims, endpoint=None, timeout=120):
    import time as _time
    if _time.time() < _BREAKER["open_until"]:
        raise RuntimeError("canon circuit breaker open")
    body = json.dumps({
        "model": CANON_MODEL,
        "temperature": 0,
        "max_tokens": 3000,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content":
                f"Document text:\n{text}\n\nClaims:\n{json.dumps(claims, ensure_ascii=False)}"},
        ],
    }).encode()
    req = urllib.request.Request(
        endpoint or CANON_ENDPOINT, data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content = json.loads(r.read())["choices"][0]["message"]["content"] or ""
        _BREAKER["fails"] = 0
    except Exception:
        _BREAKER["fails"] += 1
        if _BREAKER["fails"] >= 5:
            _BREAKER["open_until"] = _time.time() + 60
            _BREAKER["fails"] = 0
        raise
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        raise ValueError("canon returned unparseable output")
    return json.loads(m.group(0)).get("verdicts", [])


def main(listen=("127.0.0.1", 8788)):
    Handler.con = store.connect()
    ThreadingHTTPServer(listen, Handler).serve_forever()


if __name__ == "__main__":
    main((os.environ.get("API_HOST", "127.0.0.1"), int(os.environ.get("API_PORT", 8788))))
