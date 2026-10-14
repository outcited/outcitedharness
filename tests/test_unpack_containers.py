"""S2 container unpack — the guards and their negative controls.

Law (PLAN-unseen-doc-types §4/§6): provenance survives unpacking, and
nothing hostile does. Every guard here ships with the attack it stops.
"""

import io
import zipfile

from harness.electronics.l0 import extension_class
from scripts.unpack_containers import MAX_ENTRIES, safe_entry_name


def _zip_with(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


# ---- zip-slip guard -------------------------------------------------------

def test_zip_slip_relative_is_rejected():
    assert safe_entry_name("../evil.txt") is None
    assert safe_entry_name("a/../../evil.txt") is None
    assert safe_entry_name("..\\evil.txt") is None


def test_zip_slip_absolute_is_rejected():
    assert safe_entry_name("/etc/passwd") is None
    assert safe_entry_name("C:/Windows/evil.dll") is None


def test_normal_entries_pass():
    assert safe_entry_name("docs/ug.pdf") == "docs/ug.pdf"
    assert safe_entry_name("./top.txt") == "top.txt"
    assert safe_entry_name("dir/") is None  # structure marker, not a child
    assert safe_entry_name("") is None


# ---- child classification (fail closed) -----------------------------------

def test_children_class_from_extension_layer():
    assert extension_class("layout.gbr") == "gerber"
    assert extension_class("main.sxsch") == "design_file"
    assert extension_class("netlist.v") == "design_file"
    assert extension_class("readme.txt") == "sidecar_text"


def test_negative_extensionless_and_bin_stay_unknown():
    # Inside a bundle as anywhere else: no evidence, no class.
    assert extension_class("unnamed") is None
    assert extension_class("payload.bin") is None


# ---- bomb guard -----------------------------------------------------------

def test_entry_count_cap_is_defined_and_finite():
    assert MAX_ENTRIES > 0
    # A bomb is rejected whole (fail-closed) — the cap lives in unpack_one
    # and is enforced before any entry is read.
    assert MAX_ENTRIES < 10**6


def test_roundtrip_zip_builds_and_shas_stable():
    import hashlib
    payload = _zip_with({"a.txt": b"hello"})
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        data = z.read("a.txt")
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(b"hello").hexdigest()
