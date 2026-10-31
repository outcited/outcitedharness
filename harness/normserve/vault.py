"""Vault re-verification: a claim's doc_sha must match its vaulted blob.

The vault layout is blobs/<xx>/<xx>/<sha>.pdf under the vault root
(MFG_DOSIER_FACTORY handoff). We re-hash the file and compare — a claim
whose doc_sha doesn't re-verify is rejected by the loader (issue #64).
"""

import hashlib
from pathlib import Path


def blob_path(vault_root: str, sha256: str) -> Path:
    sha = sha256.lower()
    return Path(vault_root) / "blobs" / sha[0:2] / sha[2:4] / f"{sha}.pdf"


def verify_sha256(vault_root: str, sha256: str):
    """Return (ok, detail). ok=True iff the blob exists and re-hashes to
    exactly sha256. Missing blob and hash mismatch both fail closed."""
    p = blob_path(vault_root, sha256)
    if not p.exists():
        return False, f"vault blob missing: {p}"
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual != sha256.lower():
        return False, f"vault hash mismatch: claimed {sha256} actual {actual}"
    return True, "verified"
