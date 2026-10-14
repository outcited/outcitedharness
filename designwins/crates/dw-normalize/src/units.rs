//! Canonical unit multipliers — `PRD-NORMALIZE` §5.5.
//!
//! Frozen. Applied at parse time so that `500 mV` and `0.5 V` are the same
//! [`Quantity`]. Never inferred: a unit that is not in a table yields
//! [`Scale::Unknown`], which makes the parse fail closed rather than guess.

use dw_types::Unit;

/// What a printed unit token scaled to.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Scale {
    /// Recognised, scaled by this factor to the canonical unit.
    Known(f64),
    /// Printed but not in the vocabulary. Fails closed.
    Unknown,
}

/// Normalise a printed unit token the way the reference does.
///
/// Collapses whitespace, folds `μ` to `µ`, and folds `Ω` to `ohm`, then lowercases.
#[must_use]
pub fn unit_token(text: &str) -> String {
    let collapsed: String = text.chars().filter(|c| !c.is_whitespace()).collect();
    collapsed
        .replace('μ', "µ")
        .replace('Ω', "ohm")
        .to_lowercase()
}

fn lookup(kind: Kind, tok: &str) -> Scale {
    let factor = match kind {
        Kind::Volt => match tok {
            "mv" => 1e-3,
            "v" => 1.0,
            "kv" => 1e3,
            _ => return Scale::Unknown,
        },
        Kind::Ampere => match tok {
            "na" => 1e-9,
            "µa" => 1e-6,
            "ma" => 1e-3,
            "ka" => 1e3,
            "a" => 1.0,
            _ => return Scale::Unknown,
        },
        Kind::Hertz => match tok {
            "hz" => 1.0,
            "khz" => 1e3,
            "mhz" => 1e6,
            "ghz" => 1e9,
            _ => return Scale::Unknown,
        },
        Kind::Ohm => match tok {
            "mohm" => 1e-3,
            "ohm" => 1.0,
            "kohm" => 1e3,
            _ => return Scale::Unknown,
        },
        Kind::Henry => match tok {
            "nh" => 1e-9,
            "µh" => 1e-6,
            "mh" => 1e-3,
            "h" => 1.0,
            _ => return Scale::Unknown,
        },
        // °C and °C/W carry no multiplier.
        Kind::Celsius | Kind::CelsiusPerWatt => 1.0,
        Kind::AmperePerSecond => {
            // `a/<time>` — the denominator is the scale.
            let Some(rest) = tok.strip_prefix("a/") else {
                return Scale::Unknown;
            };
            match rest {
                "s" => 1.0,
                "ms" => 1e3,
                "µs" => 1e6,
                "ns" => 1e9,
                _ => return Scale::Unknown,
            }
        }
        // D10 additive kinds (2026-10-12) — frozen multipliers, evidence in
        // `DECISIONS.md`. A token outside its own kind's table stays
        // `Scale::Unknown`: `RDS(ON)`-style cross-kind prints can never coerce.
        Kind::Coulomb => match tok {
            "nc" => 1e-9,
            "µc" | "uc" => 1e-6,
            "mc" => 1e-3,
            "c" => 1.0,
            _ => return Scale::Unknown,
        },
        Kind::Farad => match tok {
            "pf" => 1e-12,
            "nf" => 1e-9,
            "µf" | "uf" => 1e-6,
            "mf" => 1e-3,
            "f" => 1.0,
            _ => return Scale::Unknown,
        },
        Kind::Second => match tok {
            "ns" => 1e-9,
            "µs" | "us" => 1e-6,
            "ms" => 1e-3,
            "s" => 1.0,
            _ => return Scale::Unknown,
        },
        Kind::Watt => match tok {
            "mw" => 1e-3,
            "w" => 1.0,
            "kw" => 1e3,
            _ => return Scale::Unknown,
        },
        Kind::Joule => match tok {
            "mj" => 1e-3,
            "j" => 1.0,
            _ => return Scale::Unknown,
        },
    };
    Scale::Known(factor)
}

/// The eight unit kinds of the frozen vocabulary (`PRD-NORMALIZE` §5.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Kind {
    /// Volts.
    Volt,
    /// Amperes.
    Ampere,
    /// Hertz.
    Hertz,
    /// Degrees Celsius.
    Celsius,
    /// Degrees Celsius per watt.
    CelsiusPerWatt,
    /// Ohms.
    Ohm,
    /// Henrys.
    Henry,
    /// Amperes per second (`di/dt`).
    AmperePerSecond,
    /// Coulombs (gate charge) — D10.
    Coulomb,
    /// Farads (capacitances) — D10.
    Farad,
    /// Seconds (switching times) — D10.
    Second,
    /// Watts (power dissipation) — D10.
    Watt,
    /// Joules (avalanche energy) — D10.
    Joule,
}

impl Kind {
    /// The canonical [`Unit`] this kind scales to.
    #[must_use]
    pub const fn unit(self) -> Unit {
        match self {
            Self::Volt => Unit::Volt,
            Self::Ampere => Unit::Ampere,
            Self::Hertz => Unit::Hertz,
            Self::Celsius | Self::CelsiusPerWatt => Unit::Celsius,
            Self::Ohm => Unit::Ohm,
            Self::Henry => Unit::Henry,
            Self::AmperePerSecond => Unit::None,
            Self::Coulomb => Unit::Coulomb,
            Self::Farad => Unit::Farad,
            Self::Second => Unit::Second,
            Self::Watt => Unit::Watt,
            Self::Joule => Unit::Joule,
        }
    }

    /// Every kind, in index order. Must match [`Self::index`].
    pub const ALL: [Self; 13] = [
        Self::Volt,
        Self::Ampere,
        Self::Hertz,
        Self::Celsius,
        Self::CelsiusPerWatt,
        Self::Ohm,
        Self::Henry,
        Self::AmperePerSecond,
        Self::Coulomb,
        Self::Farad,
        Self::Second,
        Self::Watt,
        Self::Joule,
    ];

    /// Stable index into the value-regex table. Exhaustive, so adding a kind
    /// without a regex is a compile error, not a runtime miss.
    #[must_use]
    pub const fn index(self) -> usize {
        match self {
            Self::Volt => 0,
            Self::Ampere => 1,
            Self::Hertz => 2,
            Self::Celsius => 3,
            Self::CelsiusPerWatt => 4,
            Self::Ohm => 5,
            Self::Henry => 6,
            Self::AmperePerSecond => 7,
            Self::Coulomb => 8,
            Self::Farad => 9,
            Self::Second => 10,
            Self::Watt => 11,
            Self::Joule => 12,
        }
    }

    /// Stable key used in the typed output (`_PREFIX` in the reference).
    #[must_use]
    pub const fn prefix(self) -> &'static str {
        match self {
            Self::Volt => "v",
            Self::Ampere => "a",
            Self::Hertz => "hz",
            Self::Celsius => "c",
            Self::CelsiusPerWatt => "cw",
            Self::Ohm => "o",
            Self::Henry => "ind",
            Self::AmperePerSecond => "dds",
            Self::Coulomb => "q",
            Self::Farad => "cap",
            Self::Second => "tms",
            Self::Watt => "pw",
            Self::Joule => "en",
        }
    }
}

/// Scale a printed unit token to canonical units.
#[must_use]
pub fn scale(kind: Kind, unit_text: Option<&str>) -> Scale {
    let Some(text) = unit_text else {
        return Scale::Unknown;
    };
    let tok = unit_token(text);
    lookup(kind, &tok)
}

#[cfg(test)]
// Tests assert freely: they index, unwrap, and match on single variants to
// prove the laws. Those restrictions are kept in library code and are noise
// here.
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::wildcard_enum_match_arm,
    clippy::match_wildcard_for_single_variants,
    clippy::match_same_arms,
    clippy::too_many_lines
)]
mod tests {
    use super::*;

    #[test]
    fn unit_token_folds_unicode_and_whitespace() {
        assert_eq!(unit_token(" m V "), "mv");
        assert_eq!(unit_token("μA"), "µa");
        assert_eq!(unit_token("mΩ"), "mohm");
    }

    #[test]
    fn voltage_scales() {
        assert_eq!(scale(Kind::Volt, Some("mV")), Scale::Known(1e-3));
        assert_eq!(scale(Kind::Volt, Some("V")), Scale::Known(1.0));
        assert_eq!(scale(Kind::Volt, Some("kV")), Scale::Known(1e3));
        assert_eq!(scale(Kind::Volt, Some("furlongs")), Scale::Unknown);
        assert_eq!(scale(Kind::Volt, None), Scale::Unknown);
    }

    #[test]
    fn ampere_per_second_scales_on_denominator() {
        assert_eq!(
            scale(Kind::AmperePerSecond, Some("A/µs")),
            Scale::Known(1e6)
        );
        assert_eq!(
            scale(Kind::AmperePerSecond, Some("A/ns")),
            Scale::Known(1e9)
        );
        assert_eq!(
            scale(Kind::AmperePerSecond, Some("A/fortnight")),
            Scale::Unknown
        );
    }

    #[test]
    fn celsius_has_no_multiplier() {
        assert_eq!(scale(Kind::Celsius, Some("°C")), Scale::Known(1.0));
        assert_eq!(scale(Kind::CelsiusPerWatt, Some("°C/W")), Scale::Known(1.0));
    }

    /// PRD-NORMALIZE Gate N2: unit round-trip.
    #[test]
    fn property_unit_round_trip() {
        for (kind, printed, factor_expected) in [
            (Kind::Volt, "mV", 1e-3),
            (Kind::Ampere, "µA", 1e-6),
            (Kind::Hertz, "MHz", 1e6),
            (Kind::Ohm, "mΩ", 1e-3),
            (Kind::Henry, "µH", 1e-6),
        ] {
            let Scale::Known(factor) = scale(kind, Some(printed)) else {
                panic!("{printed} must scale");
            };
            assert!((factor - factor_expected).abs() <= 1e-18, "{printed}");
            // 500 printed units -> canonical, then back. Round-trip must hold to
            // the stored tolerance (PRD Gate N2).
            let printed_magnitude = 500.0_f64;
            let canonical = printed_magnitude * factor;
            let back = canonical / factor;
            assert!((back - printed_magnitude).abs() <= 1e-9, "{printed}");
            assert!(
                (canonical - printed_magnitude * factor_expected).abs() <= 1e-15,
                "{printed}"
            );
        }
    }
}
