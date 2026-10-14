//! The 8 response schemas (`RESPONSE_SCHEMAS` in `local_model.py`).
//!
//! Each schema is a JSON-Schema-style dict used both by [`validate_response`]
//! and embedded in [`local_prompt`]'s output contract. Reproduced verbatim —
//! the prompt bytes depend on `json.dumps(schema, sort_keys=True)`.

use serde_json::{json, Value};
use std::sync::OnceLock;

/// Return the schema for a capability name.
///
/// # Panics
///
/// Unknown capability names are a caller bug — the reference's
/// `RESPONSE_SCHEMAS[capability]` raises `KeyError` under the same condition.
#[must_use]
pub fn schema_for(capability: &str) -> &'static Value {
    let map = response_schemas();
    map.get(capability)
        .unwrap_or_else(|| panic!("unsupported capability: {capability}"))
}

/// The full schema map (capability name -> schema dict).
#[must_use]
pub fn response_schemas() -> &'static Value {
    static SCHEMAS: OnceLock<Value> = OnceLock::new();
    SCHEMAS.get_or_init(|| {
        let mut map = serde_json::Map::new();
        map.insert("pin_or_ball".into(), pin_or_ball());
        map.insert("pin_semantics".into(), pin_semantics());
        map.insert("parametrics".into(), parametrics());
        map.insert("power_modes".into(), power_modes());
        map.insert("typical_characteristics".into(), typical_characteristics());
        map.insert("series_summary".into(), series_summary());
        map.insert("opn_decoder".into(), opn_decoder());
        Value::Object(map)
    })
}

fn pin_or_ball() -> Value {
    json!({
        "type": "object",
        "required": ["package", "pins"],
        "properties": {
            "package": {"type": ["string", "null"]},
            "package_as_read": {"type": ["string", "null"]},
            "pins": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["pin_no", "name"],
                    "properties": {
                        "pin_no": {"type": ["string", "integer"]},
                        "name": {"type": "string"}
                    }
                }
            }
        }
    })
}

fn pin_semantics() -> Value {
    json!({
        "type": "object",
        "required": ["pins"],
        "properties": {
            "package_as_read": {"type": ["string", "null"]},
            "pins": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["pin_no", "name", "type", "dir", "supply_domain", "functions"],
                    "properties": {
                        "pin_no": {"type": ["string", "integer"]},
                        "name": {"type": "string"},
                        "type": {"type": ["string", "null"]},
                        "dir": {"type": ["string", "null"]},
                        "supply_domain": {"type": ["string", "null"]},
                        "functions": {"type": "array"}
                    }
                }
            }
        }
    })
}

fn parametrics() -> Value {
    json!({
        "type": "object",
        "required": ["facts"],
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["field", "value", "value_role", "unit", "conditions"],
                    "properties": {
                        "field": {"type": "string"},
                        "value": {"type": ["string", "number", "integer"]},
                        "value_role": {"type": ["string", "null"]},
                        "unit": {"type": ["string", "null"]},
                        "conditions": {"type": "object"}
                    }
                }
            }
        }
    })
}

fn power_modes() -> Value {
    json!({
        "type": "object",
        "required": ["modes"],
        "properties": {
            "modes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["mode", "value", "unit", "value_role", "conditions"],
                    "properties": {
                        "mode": {"type": "string"},
                        "value": {"type": ["string", "number", "integer"]},
                        "unit": {"type": ["string", "null"]},
                        "value_role": {"type": ["string", "null"]},
                        "conditions": {"type": "object"}
                    }
                }
            }
        }
    })
}

fn typical_characteristics() -> Value {
    json!({
        "type": "object",
        "required": ["title", "axes", "series"],
        "properties": {
            "title": {"type": ["string", "null"]},
            "axes": {
                "type": "object",
                "required": ["x", "y"],
                "properties": {
                    "x": {
                        "type": "object",
                        "required": ["label", "unit"],
                        "properties": {
                            "label": {"type": ["string", "null"]},
                            "unit": {"type": ["string", "null"]},
                            "min": {"type": ["number", "null"]},
                            "max": {"type": ["number", "null"]}
                        }
                    },
                    "y": {
                        "type": "object",
                        "required": ["label", "unit"],
                        "properties": {
                            "label": {"type": ["string", "null"]},
                            "unit": {"type": ["string", "null"]},
                            "min": {"type": ["number", "null"]},
                            "max": {"type": ["number", "null"]}
                        }
                    }
                }
            },
            "series": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "condition", "points"],
                    "properties": {
                        "name": {"type": ["string", "null"]},
                        "condition": {"type": ["string", "null"]},
                        "points": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["x", "y"],
                                "properties": {
                                    "x": {"type": "number"},
                                    "y": {"type": "number"}
                                }
                            }
                        }
                    }
                }
            }
        }
    })
}

fn series_summary() -> Value {
    json!({
        "type": "object",
        "required": ["summary", "characteristics", "applications"],
        "properties": {
            "summary": {"type": "string"},
            "characteristics": {"type": "array", "items": {"type": "string"}},
            "applications": {"type": "array", "items": {"type": "string"}}
        }
    })
}

fn opn_decoder() -> Value {
    json!({
        "type": "object",
        "required": ["series", "base_part", "package_code", "suffixes"],
        "properties": {
            "series": {"type": ["string", "null"]},
            "base_part": {"type": ["string", "null"]},
            "package_code": {"type": ["string", "null"]},
            "suffixes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["code", "meaning"],
                    "properties": {
                        "code": {"type": "string"},
                        "meaning": {"type": ["string", "null"]}
                    }
                }
            }
        }
    })
}
