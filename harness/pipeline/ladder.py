"""Severity ladder: verdicts to P0-P4, batch promotion rules.

P0 truth corruption -> quarantine row; >2% of batch halts the batch.
P1 warn+hold; class >5% of batch promotes the batch to P0.
P2/3/4 advisory. Nothing ever stops the line except P0-class thresholds.
"""

THRESHOLDS = {"p0_batch_halt": 0.02, "p1_class_promote": 0.05}

VERDICT_SEVERITY = {
    ("UNSUPPORTED", "value_scale"): "P0",
    ("UNSUPPORTED", "unit_mismatch"): "P0",
    ("UNSUPPORTED", "invented_symbol"): "P0",
    ("UNSUPPORTED", None): "P1",
    ("NOT_IN_DOC", None): "P1",
    ("AMBIGUOUS", None): "P1",
}


def severity_for(verdict: str, failure_class: str = None) -> str:
    return VERDICT_SEVERITY.get((verdict, failure_class)) or VERDICT_SEVERITY.get((verdict, None), "P2")


def _class_of(v):
    return v.get("failure_class") or v.get("reason_code")


def classify_batch(verdicts):
    """Return (row_actions, batch_action) for a list of verdict dicts.

    row_actions: list of {index, severity, action} where action is
    'quarantine' | 'hold' | 'note'.
    batch_action: 'halt' | 'hold' | 'proceed' with counts attached.
    """
    n = len(verdicts) or 1
    rows = []
    p0 = p1 = 0
    by_class = {}
    for i, v in enumerate(verdicts):
        sev = severity_for(v.get("verdict", "AMBIGUOUS"), _class_of(v))
        if sev == "P0":
            action = "quarantine"
            p0 += 1
        elif sev == "P1":
            action = "hold"
            p1 += 1
            cls = _class_of(v) or "unspecified"
            by_class[cls] = by_class.get(cls, 0) + 1
        else:
            action = "note"
        rows.append({"index": i, "severity": sev, "action": action})
    batch = "proceed"
    if p0 / n > THRESHOLDS["p0_batch_halt"]:
        batch = "halt"
    elif any(c / n > THRESHOLDS["p1_class_promote"] for c in by_class.values()):
        batch = "hold"
    return rows, {
        "batch": batch,
        "p0": p0,
        "p1": p1,
        "n": len(verdicts),
        "p0_rate": round(p0 / n, 4),
    }
