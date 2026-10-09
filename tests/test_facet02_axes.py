"""SI normalization + axis registry tests (PRD-FACET-02 R2)."""

from __future__ import annotations

from harness.search import axes


def test_mega_versus_milli_case_sensitivity():
    # the regression that the MCU journey caught: MHz is NOT milli-hertz
    assert axes.si_normalize(80, "MHz") == (80e6, "hz")
    assert axes.si_normalize(80, "mHz")[0] == 0.08
    assert axes.si_normalize(2.5, "GHz") == (2.5e9, "hz")


def test_resistance_and_current_prefixes():
    v, u = axes.si_normalize(20, "mΩ")
    assert abs(v - 0.02) < 1e-12 and u == "ohm"
    v, u = axes.si_normalize(3.9, "mΩ")
    assert abs(v - 0.0039) < 1e-12
    assert axes.si_normalize(3, "µA") == (3e-6, "a")
    assert axes.si_normalize(3, "μA") == (3e-6, "a")   # both micro signs
    assert axes.si_normalize(17, "nA") == (17e-9, "a")


def test_charge_is_coulomb_not_celsius():
    v, u = axes.si_normalize(54, "nC")
    assert abs(v - 54e-9) < 1e-18 and u == "c"
    v, u = axes.si_normalize(292, "µC")
    assert u == "c" and abs(v - 292e-6) < 1e-12


def test_temperature_forms_unify():
    assert axes.si_normalize(-55, "°C") == (-55, "degc")
    assert axes.si_normalize(-55, "℃") == (-55, "degc")
    assert axes.si_normalize(-55, "C") == (-55, "degc")


def test_composites_and_percent_pass_through_unscaled():
    v, u = axes.si_normalize(40, "°C/W")
    assert v == 40 and u == "degc/w"
    v, u = axes.si_normalize(0.5, "K/W")
    assert v == 0.5 and u == "k/w"
    assert axes.si_normalize(91.5, "%") == (91.5, "%")


def test_bytes_stay_at_printed_scale():
    # KB/MB are never SI-rescaled (mB would be millibytes — nonsense)
    v, u = axes.si_normalize(256, "KB")
    assert v == 256 and u == "kb"


def test_symbol_normalization_merges_print_variants():
    assert axes.norm_symbol("RDS(ON)") == axes.norm_symbol("R_DS(ON)") \
        == "RDSON"
    assert axes.axis_for("R_DS(ON)").name == "rds_on_ohm"
    assert axes.axis_for("RDS(ON)").name == "rds_on_ohm"
    assert axes.axis_for("VGS(TH)") is not None
    assert axes.axis_for("NOT_A_SYMBOL") is None


def test_axes_are_category_scoped():
    power = {a.name for a in axes.axes_for_category("power")}
    mcu = {a.name for a in axes.axes_for_category("mcu")}
    conn = {a.name for a in axes.axes_for_category("connectors")}
    assert "vds_rating_v" in power and "flash_kb" not in power
    assert "flash_kb" in mcu and "core" in mcu
    assert "pitch_mm" in conn and "positions" in conn
    assert axes.axes_for_category(None) == []


def test_value_typing_keeps_qualifier_and_condition():
    axis = axes.axis_for("RDS(ON)")
    typed = axes.type_claim_value(
        value=20, unit="mΩ", qualifier="max", condition_norm="vgs=10 v",
        condition_typed={"keys": {"vgs_v": 10.0}}, axis=axis)
    assert typed["kind"] == "inequality" and typed["min_max"] == "max"
    assert typed["rating_class"] == "rated_limit"
    assert typed["condition"] == "vgs=10 v"
    assert typed["condition_typed"] == {"vgs_v": 10.0}
    assert abs(typed["value"] - 0.02) < 1e-12
    # typ stays a typical-class point, never a limit
    typ = axes.type_claim_value(value=1.2, unit="V", qualifier="typ",
                                condition_norm="", condition_typed=None,
                                axis=axes.axis_for("VGS(TH)"))
    assert typ["kind"] == "point" and typ["rating_class"] == "typical"
    # non-numeric is unknown, never coerced
    unk = axes.type_claim_value(value=None, unit="V", qualifier=None,
                                condition_norm="", condition_typed=None,
                                axis=axes.axis_for("VDSS"))
    assert unk["kind"] == "unknown"