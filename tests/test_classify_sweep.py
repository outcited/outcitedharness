"""S1 classification sweep — positives and the negative controls.

The law (PLAN-unseen-doc-types 2026-10-12): re-label only where evidence
exists. Every rule here that assigns a class ships with a control that
proves the sweep does not invent one.
"""

from harness.electronics.l0 import classify_document, extension_class
from scripts.classify_sweep import batch_of, prior_for


# ---- extension classes ----------------------------------------------------

def test_extension_forces_container_over_content():
    # A zip mentioning "datasheet" in any bytes is still a container.
    assert extension_class("bundle.zip") == "container"
    assert classify_document("datasheet electrical characteristics", "bundle.zip", "") == "container"


def test_extension_image_sheet_sidecar_eda():
    assert extension_class("fig3.png") == "image_only"
    assert extension_class("parametrics.xlsx") == "tabular_sheet"
    assert extension_class("readme.txt") == "sidecar_text"
    assert extension_class("amp.s2p") == "eda_model"
    assert extension_class("model.lib") == "eda_model"


def test_negative_bin_is_never_eda_model():
    # A firmware blob is not a simulation model — fail closed, no guess.
    assert extension_class("fw_update.bin") is None
    assert classify_document("", "fw_update.bin", "") == "unknown"


def test_negative_pdf_is_not_forced():
    # Documents classify on content, never by extension forcing.
    assert extension_class("FDBL0065N40-D.pdf") is None


# ---- batch-name priors ----------------------------------------------------

def test_batch_priors_the_name_carries_truth():
    assert batch_of("/x/datasheet-tail-20261007/blobs/aa/bb/sha.pdf") == "datasheet-tail-20261007"
    assert prior_for("datasheet-tail-20261007") == "datasheet"
    assert prior_for("pcn-lifecycle-20261007") == "pcn"
    assert prior_for("refdesigns-eval-20261007") == "reference_design"
    assert prior_for("mcu-manuals-20260908") == "reference_manual"


def test_negative_unnamed_batches_stay_unknown():
    # `unassigned` / `incremental` / `holdout` name no class — no prior.
    assert prior_for("unassigned-l0-20261007") is None
    assert prior_for("incremental-20261009") is None
    assert prior_for("holdout_pins_40_20260906") is None
    assert prior_for("") is None


def test_negative_prior_never_beats_content():
    # Content classification is first and stays first — a prior is only a
    # fallback for what content could not decide.
    assert classify_document("Silicon Errata — known exceptions", "sha-blob.pdf", "") == "errata"
