#!/usr/bin/env python3
"""Emit the hand-labeled condition-model gold fixture (v1).

Reads the stratified sample (scripts/sample_condition_gold.py output) and
joins the hand-label table below keyed by verbatim string. Labels were
written by reading each string against the parse contract documented in
harness/electronics/condition_model.py — never by running the parser.

Output rows: {vendor, stem, freq, condition_verbatim, expected}.
expected is {"reject": cls} or {"keys": {...}, mode/approx/sym/bare/
comparators/residue when non-empty}.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MHZ = 1_000_000.0
KHZ = 1_000.0

R_DASH = {"reject": "dash"}
R_PROSE = {"reject": "no_anchors"}
R_UNITS = {"reject": "units_absent"}
R_AMBIG = {"reject": "ambiguous_symbols"}


def L(**kw):
    return kw


LABELS = {
    # --- include-list edges -------------------------------------------------
    "-": R_DASH,
    "–": R_DASH,
    "static": L(keys={}, mode="static"),
    "static;": L(keys={}, mode="static"),
    "AC (f>1 Hz)": L(keys={"f_hz": 1.0}, mode="ac", comparators=["f>1 Hz"]),
    "A ceramic capacitor is recommended.": R_PROSE,
    "minimal footprint": R_PROSE,
    "Ta = 25°C ,unless otherwise specified": L(keys={"ta_c": 25.0},
        residue=["unless otherwise specified"]),
    "Ta=25℃": L(keys={"ta_c": 25.0}),
    "T C=25 °C": L(keys={"tc_c": 25.0}),
    "V GS=0 V, V DS=25 V, f =1 MHz": L(keys={"vgs_v": 0.0, "vds_v": 25.0, "f_hz": MHZ}),
    "V_GS=20 V, V_DS=0 V": L(keys={"vgs_v": 20.0, "vds_v": 0.0}),
    "VDD ⋍ 300V": L(keys={"vdd_v": 300.0}, approx=["vdd_v"]),
    "VGS = ±20V, VDS = 0V": L(keys={"vgs_v": 20.0, "vds_v": 0.0}, sym=["vgs_v"]),
    "TC=100°C": L(keys={"tc_c": 100.0}),
    "IOUT=0mA": L(keys={"iout_a": 0.0}),
    "VEN=0V, OFF mode": L(keys={"ven_v": 0.0}, residue=["OFF mode"]),
    "VDS = 10V, ID = 1mA": L(keys={"vds_v": 10.0, "id_a": 0.001}),
    "f = 1MHz, open drain": L(keys={"f_hz": MHZ}, residue=["open drain"]),
    # --- high-frequency population ------------------------------------------
    "TC=25°C": L(keys={"tc_c": 25.0}),
    "TC=25 °C": L(keys={"tc_c": 25.0}),
    "VGS = 0V": L(keys={"vgs_v": 0.0}),
    "Tc = 25°C": L(keys={"tc_c": 25.0}),
    "T_C=25 °C": L(keys={"tc_c": 25.0}),
    "VGS = 0V, ID = 1mA": L(keys={"vgs_v": 0.0, "id_a": 0.001}),
    "VGS=20 V, VDS=0 V": L(keys={"vgs_v": 20.0, "vds_v": 0.0}),
    "V GS=0V, V DS=25V, f =1MHz": L(keys={"vgs_v": 0.0, "vds_v": 25.0, "f_hz": MHZ}),
    "VGS=0V, VDS=400V, f=250kHz": L(keys={"vgs_v": 0.0, "vds_v": 400.0, "f_hz": 250.0 * KHZ}),
    "VGS = 10V": L(keys={"vgs_v": 10.0}),
    "V GS = 0 V, V DS = 20 V, f = 1 MHz": L(keys={"vgs_v": 0.0, "vds_v": 20.0, "f_hz": MHZ}),
    "VGS=0 V, VDS=40 V, f=1 MHz": L(keys={"vgs_v": 0.0, "vds_v": 40.0, "f_hz": MHZ}),
    "VGS=20V, VDS=0V": L(keys={"vgs_v": 20.0, "vds_v": 0.0}),
    "VGS=0V, ID=1mA": L(keys={"vgs_v": 0.0, "id_a": 0.001}),
    "V GS=20 V, V DS=0 V": L(keys={"vgs_v": 20.0, "vds_v": 0.0}),
    "T C=25°C": L(keys={"tc_c": 25.0}),
    "VGS=0 V, VDS=30 V, f=1 MHz": L(keys={"vgs_v": 0.0, "vds_v": 30.0, "f_hz": MHZ}),
    "RG = 10Ω": L(keys={"rg_ohm": 10.0}),
    "Ta = 25°C": L(keys={"ta_c": 25.0}),
    "f = 1MHz": L(keys={"f_hz": MHZ}),
    "TC = 25℃": L(keys={"tc_c": 25.0}),
    "Ta = 25℃": L(keys={"ta_c": 25.0}),
    "STBY=1.5V": L(keys={"stby_v": 1.5}),
    "Ceramic capacitor recommended": R_PROSE,
    # --- mid band ------------------------------------------------------------
    "ID = 35A": L(keys={"id_a": 35.0}),
    "V =12 V, I =20 A, V =0 to 4.5 V": R_AMBIG,
    "f=1MHz": L(keys={"f_hz": MHZ}),
    "VDD=300V, VGS=13V, ID=8A, RG=10Ω; see table 9": L(
        keys={"vdd_v": 300.0, "vgs_v": 13.0, "id_a": 8.0, "rg_ohm": 10.0},
        residue=["see table 9"]),
    "VGS = 0V, VDS = 25V, ƒ = 1.0MHz, See Fig.7": L(
        keys={"vgs_v": 0.0, "vds_v": 25.0, "f_hz": MHZ}, residue=["See Fig.7"]),
    "VDS = 200V, VGS = 0V": L(keys={"vds_v": 200.0, "vgs_v": 0.0}),
    "V GS=10V, I D=20A": L(keys={"vgs_v": 10.0, "id_a": 20.0}),
    "V DD = 50 V, I D = 88 A, V GS = 0 to 10 V": L(
        keys={"vdd_v": 50.0, "id_a": 88.0, "vgs_v": [0.0, 10.0]}),
    "VDD = 800 V, ID = 40 A, VGS = 0/18 V, RG,ext = 2.3 Ω, Lσ = 15 nH, "
    "diode: body diode at VGS = 0 V, Tvj = 175 °C": L(
        keys={"vdd_v": 800.0, "id_a": 40.0, "vgs_v": [0.0, 18.0],
              "rg_ext_ohm": 2.3, "lsigma_h": 1.5e-08, "tvj_c": 175.0},
        residue=["diode: body diode at VGS = 0 V"]),
    "VDD ≤ 800 V, VDS,peak < 1200 V, VGS(on) = 15 V, Tvj(start) = 25 °C": L(
        keys={"vdd_v": 800.0, "vds_peak_v": 1200.0, "vgs_on_v": 15.0,
              "tvj_start_c": 25.0},
        comparators=["VDD ≤ 800 V", "VDS,peak < 1200 V"]),
    "VDD=400V, ID=8.5A, VGS=0 to 10V": L(
        keys={"vdd_v": 400.0, "id_a": 8.5, "vgs_v": [0.0, 10.0]}),
    "V DD = 20 V, I D = 90 A, V GS = 0 to 10 V": L(
        keys={"vdd_v": 20.0, "id_a": 90.0, "vgs_v": [0.0, 10.0]}),
    "V GS=0 V, V DS=40 V, \nf =1 MHz": L(
        keys={"vgs_v": 0.0, "vds_v": 40.0, "f_hz": MHZ}),
    "ID=10.2A; VDD=50V; see table 10": L(
        keys={"id_a": 10.2, "vdd_v": 50.0}, residue=["see table 10"]),
    "VDD = 800 V, ID = 6 A, VGS = 0/18 V, RG,ext = 2.3 Ω, Lσ = 15 nH, "
    "diode: body diode at VGS = 0 V, Tvj = 25 °C": L(
        keys={"vdd_v": 800.0, "id_a": 6.0, "vgs_v": [0.0, 18.0],
              "rg_ext_ohm": 2.3, "lsigma_h": 1.5e-08, "tvj_c": 25.0},
        residue=["diode: body diode at VGS = 0 V"]),
    "VR=20 V, IF=50 A, diF/dt=1000 A/µs": L(
        keys={"vr_v": 20.0, "if_a": 50.0, "difdt_a_per_s": 1e9}),
    "T_A=25 °C, R_thJA=40 °C/W": L(keys={"ta_c": 25.0, "rthja_c_per_w": 40.0}),
    "VDD = 800 V, ISD = 6 A, VGS = -5 V, RG,ext = 2.3 Ω, "
    "Qfr includes also QC, Tvj = 25 °C": L(
        keys={"vdd_v": 800.0, "isd_a": 6.0, "vgs_v": -5.0,
              "rg_ext_ohm": 2.3, "tvj_c": 25.0},
        residue=["Qfr includes also QC"]),
    "TJ = 125°C IF = 100A,": L(keys={"tj_c": 125.0, "if_a": 100.0}),
    "VGS=10V, ID=2.2A, Tj=150°C": L(keys={"vgs_v": 10.0, "id_a": 2.2, "tj_c": 150.0}),
    "VGS=10V, ID=10.4A, Tj=150°C": L(keys={"vgs_v": 10.0, "id_a": 10.4, "tj_c": 150.0}),
    "V DS=-32V, V GS=0V,  T j=125°C2)": L(
        keys={"vds_v": -32.0, "vgs_v": 0.0, "tj_c": 125.0}, residue=["2)"]),
    "RG= 3.9": R_UNITS,
    "(TO263)": R_PROSE,
    "ID=72 A, RGS=25 Ω": L(keys={"id_a": 72.0, "rgs_ohm": 25.0}),
    "VDD ≤ 800 V, VDS,peak < 1200 V, Tvj(start) = 25 °C, RG,ext = 2 Ω; "
    "VGS(on) = 18 V": L(
        keys={"vdd_v": 800.0, "vds_peak_v": 1200.0, "tvj_start_c": 25.0,
              "rg_ext_ohm": 2.0, "vgs_on_v": 18.0},
        comparators=["VDD ≤ 800 V", "VDS,peak < 1200 V"]),
    "ID = 44A, VDS =0V, VGS = 10V": L(keys={"id_a": 44.0, "vds_v": 0.0, "vgs_v": 10.0}),
    "VR=15 V, IF=|IS|, diF/dt=100 A/µs": L(
        keys={"vr_v": 15.0, "difdt_a_per_s": 1e8}, residue=["IF=|IS|"]),
    "VGS=4.5 V, TA=25 °C, RthJA=60 K/W": L(
        keys={"vgs_v": 4.5, "ta_c": 25.0, "rthja_c_per_w": 60.0}),
    "I_D=30 A, R=25 Ω": L(keys={"id_a": 30.0, "r_ohm": 25.0}),
    "I_AS=46 A, R=25 Ω": L(keys={"i_as_a": 46.0, "r_ohm": 25.0}),
    "V GS=4.5 V, I D=1.7 A": L(keys={"vgs_v": 4.5, "id_a": 1.7}),
    "V_DS = V_GS, I_D = 3.0 mA, T_j = 175°C": L(
        keys={"id_a": 0.003, "tj_c": 175.0}, residue=["V_DS = V_GS"]),
    "VGS=0 ,VDS=80": R_UNITS,
    "VDD ⋍ 300V, VGS = 10V": L(keys={"vdd_v": 300.0, "vgs_v": 10.0}, approx=["vdd_v"]),
    "VDS = 0V to 480V": L(keys={"vds_v": [0.0, 480.0]}),
    "VGS = ±30V, VDS = 0V": L(keys={"vgs_v": 30.0, "vds_v": 0.0}, sym=["vgs_v"]),
    "Vo=0V": L(keys={"vout_v": 0.0}),
    "VIN=4.0V, STBY=0V, VOUT=4.0V": L(
        keys={"vin_v": 4.0, "stby_v": 0.0, "vout_v": 4.0}),
    "Vo=VOUT*0.95": R_PROSE,
    "VDS = 600V, VGS = 0V": L(keys={"vds_v": 600.0, "vgs_v": 0.0}),
    "Ta=25°C": L(keys={"ta_c": 25.0}),
    "IOUT=0.01mA to 100mA": L(keys={"iout_a": [1e-05, 0.1]}),
    "VDS = VGS, ID = 1mA": L(keys={"id_a": 0.001}, residue=["VDS = VGS"]),
    "VGS = 10V, ID = 18.1A, Tj = 125°C": L(
        keys={"vgs_v": 10.0, "id_a": 18.1, "tj_c": 125.0}),
    "VDS = 600V, VGS = 0V, Tj = 125°C": L(
        keys={"vds_v": 600.0, "vgs_v": 0.0, "tj_c": 125.0}),
    "VDD ⋍ 300V, ID = 20A": L(keys={"vdd_v": 300.0, "id_a": 20.0}, approx=["vdd_v"]),
    "VDD ⋍ -15V,VGS = -10V": L(
        keys={"vdd_v": -15.0, "vgs_v": -10.0}, approx=["vdd_v"]),
    "1.0V≦VOUT＜1.2V(IOUT=200mA)": L(
        keys={"vout_v": [1.0, 1.2], "iout_a": 0.2},
        comparators=["1.0V≦VOUT＜1.2V"]),
    "Tc = 25℃": L(keys={"tc_c": 25.0}),
    "1s(Note 3)": R_PROSE,
    "VRR=-20 dBv,fRR=1 kHz, IOUT=10 mA, 2.5 V≤VOUT": L(
        keys={"frr_hz": KHZ, "iout_a": 0.01, "vout_v": 2.5},
        residue=["VRR=-20 dBv"], comparators=["2.5 V≤VOUT"]),
    "3.2V≦VOUT≦3.4V(IOUT=200mA)": L(
        keys={"vout_v": [3.2, 3.4], "iout_a": 0.2},
        comparators=["3.2V≦VOUT≦3.4V"]),
    "wavesoldering for 10s": R_PROSE,
    "VDD ⋍ 300V, ID = 24A, VGS = 10V": L(
        keys={"vdd_v": 300.0, "id_a": 24.0, "vgs_v": 10.0}, approx=["vdd_v"]),
    "VDS = VGS, ID = 4.5mA": L(keys={"id_a": 0.0045}, residue=["VDS = VGS"]),
    "VGS = 0V, VDS = 100V": L(keys={"vgs_v": 0.0, "vds_v": 100.0}),
    "VDS = 10V, ID = 10A": L(keys={"vds_v": 10.0, "id_a": 10.0}),
    "VIN=VOUT+1.0V (Note 1)": R_PROSE,
    "ID = -1.5A": L(keys={"id_a": -1.5}),
    "2.9V≦VOUT≦3.1V(IOUT=200mA)": L(
        keys={"vout_v": [2.9, 3.1], "iout_a": 0.2},
        comparators=["2.9V≦VOUT≦3.1V"]),
    "25℃, VCC =( Vo+0.9V )→14.0V": L(
        keys={"t_c": 25.0}, bare=["t_c"],
        residue=["VCC =( Vo+0.9V )→14.0V"]),
    "Vo ≥ 3.0V": L(keys={"vout_v": 3.0}, comparators=["Vo ≥ 3.0V"]),
    "VDD ⋍ 300V, ID = 7A, VGS = 10V": L(
        keys={"vdd_v": 300.0, "id_a": 7.0, "vgs_v": 10.0}, approx=["vdd_v"]),
    "Ta = 25°C; VDD ⋍ 300V": L(keys={"ta_c": 25.0, "vdd_v": 300.0}, approx=["vdd_v"]),
    "VCC=5V, IO=1.0A, -40~105℃": L(
        keys={"vcc_v": 5.0, "iout_a": 1.0, "t_c": [-40.0, 105.0]}, bare=["t_c"]),
    "VDS = -10V, ID = -1.2A": L(keys={"vds_v": -10.0, "id_a": -1.2}),
    "VGS = 1.8V, ID = 3.2A": L(keys={"vgs_v": 1.8, "id_a": 3.2}),
    "VGS = 10V, ID = 6.5A, Tj  =  25°C": L(
        keys={"vgs_v": 10.0, "id_a": 6.5, "tj_c": 25.0}),
    "VDS = -5V, ID = -3.0A": L(keys={"vds_v": -5.0, "id_a": -3.0}),
    "VDD = 300V, ID = 24A": L(keys={"vdd_v": 300.0, "id_a": 24.0}),
    "VGS = -1.5V, ID = -0.6A": L(keys={"vgs_v": -1.5, "id_a": -0.6}),
    "LOW": R_PROSE,
    "VDD ⋍ 20V ID = 4.5A VGS = 10V": L(
        keys={"vdd_v": 20.0, "id_a": 4.5, "vgs_v": 10.0}, approx=["vdd_v"]),
    "VDS = 5V, ID = 3.5A": L(keys={"vds_v": 5.0, "id_a": 3.5}),
    "VGS = -4.5V, ID = -0.75A": L(keys={"vgs_v": -4.5, "id_a": -0.75}),
    "VDS = 5.0V, ID = 6.0A": L(keys={"vds_v": 5.0, "id_a": 6.0}),
    "VGS = 0V, IS = 70A": L(keys={"vgs_v": 0.0, "is_a": 70.0}),
    # --- TI band -------------------------------------------------------------
    "VGS = 0 V, VDS = 15 V, ƒ = 1 MHz": L(
        keys={"vgs_v": 0.0, "vds_v": 15.0, "f_hz": MHZ}),
    "VDS = VGS, ID = 250 μA": L(keys={"id_a": 0.00025}, residue=["VDS = VGS"]),
    "VDS = VGS, ID = 250mA": L(keys={"id_a": 0.25}, residue=["VDS = VGS"]),
    "VGS = 0 V, VDS = 6 V, ƒ = 1 MHz": L(
        keys={"vgs_v": 0.0, "vds_v": 6.0, "f_hz": MHZ}),
    "VGS = 0V, VDS = 12.5V, f = 1MHz": L(
        keys={"vgs_v": 0.0, "vds_v": 12.5, "f_hz": MHZ}),
    "VGS = 0 V, VDS = 12.5 V, ƒ = 1 MHz": L(
        keys={"vgs_v": 0.0, "vds_v": 12.5, "f_hz": MHZ}),
    "VGS = 4.5 V": L(keys={"vgs_v": 4.5}),
    "TA = 25°C (unless otherwise stated)": L(
        keys={"ta_c": 25.0}, residue=["(unless otherwise stated)"]),
    "VDS = VGS, IDS = 250 μA": L(keys={"id_a": 0.00025}, residue=["VDS = VGS"]),
    "VDS = 15V, ID = 14A": L(keys={"vds_v": 15.0, "id_a": 14.0}),
    "VDS = VGS, IDS = 250μA": L(keys={"id_a": 0.00025}, residue=["VDS = VGS"]),
    "VGS = 0 V, VDS = 12.5 V, f = 1 MHz": L(
        keys={"vgs_v": 0.0, "vds_v": 12.5, "f_hz": MHZ}),
    "VGS = 0V, VDS = 24V": L(keys={"vgs_v": 0.0, "vds_v": 24.0}),
    "ISINK = 3mA": L(keys={"isink_a": 0.003}),
    "TA = 25°C unless otherwise stated": L(
        keys={"ta_c": 25.0}, residue=["unless otherwise stated"]),
    "VDS = 15V, IDS = 20A": L(keys={"vds_v": 15.0, "id_a": 20.0}),
    "VGS = 0 V, ID = 250 μA": L(keys={"vgs_v": 0.0, "id_a": 0.00025}),
    "VDS = 15V, ID = 22A": L(keys={"vds_v": 15.0, "id_a": 22.0}),
    "VGS = 8V": L(keys={"vgs_v": 8.0}),
    "Code 512": R_PROSE,
    "VDS = 6 V, ID = 1 A": L(keys={"vds_v": 6.0, "id_a": 1.0}),
    "VGS = 0V, ID = 250mA": L(keys={"vgs_v": 0.0, "id_a": 0.25}),
    "VGS = 0V, VDS = 6V, f = 1MHz": L(
        keys={"vgs_v": 0.0, "vds_v": 6.0, "f_hz": MHZ}),
    "Over temperature": R_PROSE,
    "VDS = 6V, ID = 1.5A": L(keys={"vds_v": 6.0, "id_a": 1.5}),
    "4.5V": R_PROSE,
    "PAP (HTQFP) 64 PINS": R_PROSE,
    "VGS = 1.8 V": L(keys={"vgs_v": 1.8}),
    "VDS = 15V, IDS = 25A": L(keys={"vds_v": 15.0, "id_a": 25.0}),
    "ID = 36 A, L = 0.1 mH, RG = 25 Ω": L(
        keys={"id_a": 36.0, "l_h": 0.0001, "rg_ohm": 25.0}),
    "VGS = 4.5 V, ID = 10 A": L(keys={"vgs_v": 4.5, "id_a": 10.0}),
    "ΔVREG/ΔTA, IREG = 10 mA": L(
        keys={"ireg_a": 0.01}, residue=["ΔVREG/ΔTA"]),
    "Continuous Drain Current (Package Limited)": R_PROSE,
    "Pulsed Drain Current(3)": R_PROSE,
    "VDS = 6 V, ID = 1.5 A": L(keys={"vds_v": 6.0, "id_a": 1.5}),
    "VDS = 13.5 V, VGS = 0 V": L(keys={"vds_v": 13.5, "vgs_v": 0.0}),
    "SW": R_PROSE,
    "VDS = 15 V, ID = 20 A": L(keys={"vds_v": 15.0, "id_a": 20.0}),
    "VGS = 10 V, ID = 40 A": L(keys={"vgs_v": 10.0, "id_a": 40.0}),
    "Outputs at midscale with no load": R_PROSE,
    "(4.5V)": R_PROSE,
    "IS = 20A, VGS = 0V": L(keys={"is_a": 20.0, "vgs_v": 0.0}),
    "RMODE < 168kΩ": R_PROSE,
    "PHP (HTQFP 48-pin)": R_PROSE,
    "VGS = 3 V, ID = 4 A": L(keys={"vgs_v": 3.0, "id_a": 4.0}),
    "External PBI capacitor": R_PROSE,
}


def main(sample: Path, out: Path) -> int:
    rows = [json.loads(l) for l in sample.read_text().splitlines() if l.strip()]
    missing = [r["condition_verbatim"] for r in rows
               if r["condition_verbatim"] not in LABELS]
    if missing:
        print("UNLABELED — refusing to emit:", file=sys.stderr)
        for m in missing:
            print(f"  {m!r}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as h:
        for r in rows:
            row = dict(r)
            row["expected"] = LABELS[r["condition_verbatim"]]
            h.write(json.dumps(row, ensure_ascii=False) + "\n")
    rejects = sum(1 for r in rows if "reject" in LABELS[r["condition_verbatim"]])
    print(f"fixture: {len(rows)} rows ({rejects} reject-labeled) -> {out}")
    return 0


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/cond-gold/sample.jsonl")
    dst = (Path(sys.argv[2]) if len(sys.argv) > 2
           else Path("tests/fixtures/gold/condition_model_v1.jsonl"))
    raise SystemExit(main(src, dst))
