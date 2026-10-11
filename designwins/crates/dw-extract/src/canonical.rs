//! Python-compatible canonical JSON — `harness.electronics.claims.canonical_json`.
//!
//! Byte-for-byte parity with
//! `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)`.
//! This feeds `stable_id`, so float formatting is load-bearing: Python writes
//! `1.5e-08` where `serde_json` writes `1.5e-8`, and a mismatch here silently
//! changes every claim id. The float repr is Python's, not Rust's, and is
//! gated by live-Python vectors (`extract_canonical_gold_v1.jsonl`).

use serde_json::Value;

use crate::sha256::sha256_hex;

/// A canonical-encoding failure (`allow_nan=False` semantics plus range rules).
#[derive(Debug, thiserror::Error)]
pub enum CanonicalError {
    /// Non-finite float; Python raises `ValueError` here.
    #[error("non-finite number cannot appear in canonical JSON")]
    NonFinite,
    /// An integer outside `i64`/`u64`; Python's ints are unbounded, serde's
    /// are not, so the common range is the contract.
    #[error("integer out of the i64/u64 canonical range")]
    IntegerOutOfRange,
    /// `stable_id` prefix outside `claim`/`verify`/`admit`/`pair`/`bundle`.
    #[error("unsupported stable ID prefix: {0}")]
    UnsupportedPrefix(String),
}

/// Encode `value` exactly as Python's `canonical_json` would.
///
/// # Errors
///
/// [`CanonicalError::NonFinite`] on NaN/±inf;
/// [`CanonicalError::IntegerOutOfRange`] on integers outside `i64`/`u64`.
pub fn canonical_json(value: &Value) -> Result<Vec<u8>, CanonicalError> {
    let mut out = Vec::new();
    write_value(value, &mut out)?;
    Ok(out)
}

/// `stable_id(prefix, value)` — `"{prefix}-{sha256(canonical_json(value))[:32]}"`.
///
/// # Errors
///
/// Propagates [`CanonicalError`]; rejects prefixes outside the reference's
/// whitelist exactly as `claims.stable_id` does.
pub fn stable_id(prefix: &str, value: &Value) -> Result<String, CanonicalError> {
    if !matches!(prefix, "claim" | "verify" | "admit" | "pair" | "bundle") {
        return Err(CanonicalError::UnsupportedPrefix(prefix.to_string()));
    }
    let hex = sha256_hex(&canonical_json(value)?);
    let short: String = hex.chars().take(32).collect();
    Ok(format!("{prefix}-{short}"))
}

fn write_value(value: &Value, out: &mut Vec<u8>) -> Result<(), CanonicalError> {
    match value {
        Value::Null => out.extend_from_slice(b"null"),
        Value::Bool(true) => out.extend_from_slice(b"true"),
        Value::Bool(false) => out.extend_from_slice(b"false"),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                out.extend_from_slice(i.to_string().as_bytes());
            } else if let Some(u) = n.as_u64() {
                out.extend_from_slice(u.to_string().as_bytes());
            } else if let Some(f) = n.as_f64() {
                out.extend_from_slice(python_float_repr(f)?.as_bytes());
            } else {
                return Err(CanonicalError::IntegerOutOfRange);
            }
        }
        Value::String(s) => write_string(s, out),
        Value::Array(items) => {
            out.push(b'[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(b',');
                }
                write_value(item, out)?;
            }
            out.push(b']');
        }
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            out.push(b'{');
            for (i, key) in keys.iter().enumerate() {
                if i > 0 {
                    out.push(b',');
                }
                write_string(key, out);
                out.push(b':');
                // Every key collected from the map resolves; the match inside
                // `write_value` reports encoding faults, not missing keys.
                if let Some(v) = map.get(*key) {
                    write_value(v, out)?;
                }
            }
            out.push(b'}');
        }
    }
    Ok(())
}

/// `ensure_ascii=False` escaping: only `"`, `\`, and control characters.
fn write_string(s: &str, out: &mut Vec<u8>) {
    out.push(b'"');
    for ch in s.chars() {
        match ch {
            '"' => out.extend_from_slice(b"\\\""),
            '\\' => out.extend_from_slice(b"\\\\"),
            '\u{8}' => out.extend_from_slice(b"\\b"),
            '\u{c}' => out.extend_from_slice(b"\\f"),
            '\n' => out.extend_from_slice(b"\\n"),
            '\r' => out.extend_from_slice(b"\\r"),
            '\t' => out.extend_from_slice(b"\\t"),
            c if (c as u32) < 0x20 => {
                out.extend_from_slice(format!("\\u{:04x}", c as u32).as_bytes());
            }
            c => out.extend_from_slice(c.to_string().as_bytes()),
        }
    }
    out.push(b'"');
}

/// Python `repr(float)` / `json.dumps` number formatting, not Rust's.
///
/// Shortest round-trip digits, then Python's notation rule: fixed-point for
/// decimal exponents `-4..=15` (`0.0001`, `1000000000000000.0`), scientific
/// otherwise with a signed, zero-padded, two-digit-minimum exponent
/// (`1e-05`, `1.5e-08`, `1e+16`). Negative zero keeps its sign (`-0.0`).
fn python_float_repr(v: f64) -> Result<String, CanonicalError> {
    if !v.is_finite() {
        return Err(CanonicalError::NonFinite);
    }
    if v == 0.0 {
        return Ok(String::from(if v.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }));
    }
    let sci = format!("{v:e}");
    let (mantissa, exp) = sci.split_once('e').ok_or(CanonicalError::NonFinite)?;
    let exp: i32 = exp.parse().map_err(|_| CanonicalError::NonFinite)?;
    let negative = mantissa.starts_with('-');
    // The sign must leave before digit extraction or "-55" formats as "--5.5".
    let mantissa = mantissa.strip_prefix('-').unwrap_or(mantissa);
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let digits = digits.trim_end_matches('0');
    let digits = if digits.is_empty() { "0" } else { digits };
    let mut body = if (-4..=15).contains(&exp) {
        if exp >= 0 {
            let int_len = usize::try_from(exp).unwrap_or(0) + 1;
            let mut integer: String = digits.to_string();
            while integer.len() < int_len {
                integer.push('0');
            }
            let (int_part, frac_part) = integer.split_at(int_len);
            let frac = if frac_part.is_empty() { "0" } else { frac_part };
            format!("{int_part}.{frac}")
        } else {
            let zeros = usize::try_from(-exp - 1).map_err(|_| CanonicalError::NonFinite)?;
            format!("0.{}{}", "0".repeat(zeros), digits)
        }
    } else {
        let head = digits.chars().next().ok_or(CanonicalError::NonFinite)?;
        let rest = &digits[1.min(digits.len())..];
        let mant = if rest.is_empty() {
            format!("{head}")
        } else {
            format!("{head}.{rest}")
        };
        let abs_exp = exp.unsigned_abs();
        let sign = if exp < 0 { '-' } else { '+' };
        format!("{mant}e{sign}{abs_exp:02}")
    };
    if negative {
        body.insert(0, '-');
    }
    Ok(body)
}

#[cfg(test)]
// Test module: asserts are the point here; unwraps on fixture data are the
// documented allowance from HANDOFF.md ("Test modules may carry a documented
// #[allow]").
#[allow(clippy::unwrap_used, clippy::expect_used, clippy::indexing_slicing)]
mod tests {
    use serde_json::json;

    use super::{canonical_json, python_float_repr, stable_id};
    use crate::CanonicalError;

    /// R1: what fails this? `1e-8` formatted the Rust way (`1.5e-8`) instead
    /// of Python's (`1.5e-08`) — silent claim-id divergence.
    #[test]
    fn float_repr_matches_python_notation() {
        assert_eq!(python_float_repr(1.5e-8).unwrap(), "1.5e-08");
        assert_eq!(python_float_repr(1e-5).unwrap(), "1e-05");
        assert_eq!(python_float_repr(1e16).unwrap(), "1e+16");
        assert_eq!(python_float_repr(-55.0).unwrap(), "-55.0");
        assert_eq!(python_float_repr(0.1).unwrap(), "0.1");
        assert_eq!(python_float_repr(0.0001).unwrap(), "0.0001");
        assert_eq!(python_float_repr(1e15).unwrap(), "1000000000000000.0");
        assert_eq!(python_float_repr(1.0).unwrap(), "1.0");
        assert_eq!(python_float_repr(-0.0).unwrap(), "-0.0");
    }

    #[test]
    fn negative_control_nonfinite_is_rejected_not_encoded() {
        assert!(matches!(
            python_float_repr(f64::NAN),
            Err(CanonicalError::NonFinite)
        ));
    }

    #[test]
    fn canonical_sorts_and_compacts_like_python() {
        let v = json!({"b": 1, "a": [true, null, "x"], "c": {"z": 1.0, "y": -55.0}});
        let bytes = canonical_json(&v).unwrap();
        assert_eq!(
            String::from_utf8(bytes).unwrap(),
            "{\"a\":[true,null,\"x\"],\"b\":1,\"c\":{\"y\":-55.0,\"z\":1.0}}"
        );
    }

    #[test]
    fn stable_id_prefix_whitelist_is_enforced() {
        assert!(matches!(
            stable_id("claim", &json!({})),
            Ok(id) if id.starts_with("claim-") && id.len() == 6 + 32
        ));
        assert!(matches!(
            stable_id("bogus", &json!({})),
            Err(CanonicalError::UnsupportedPrefix(_))
        ));
    }
}
