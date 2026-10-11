//! Ten-group Key Features grid — port of `harness/electronics/key_features_grid.py`.
//!
//! Observable behaviour only: the same rows, tiers, dedupe and typed facts the
//! reference emits for a family-document record. Python's `dict.get`/`[]` mix
//! is mirrored: absent optional keys read as the reference's `None` fallbacks;
//! absent *required* keys are [`crate::ExtractError::Malformed`], never a
//! panic.

mod helpers;
mod qualify;
mod rows;
mod tables;
mod typed;

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use helpers::{
    arr_empty, grid_eligible, infer_vendor, is_match, non_scope, norm_key, normalize_vendor,
    num_as_i64, package_or_temp, power_fact, py_str, regex_escape, set_key, sub_all, vendor_stem,
};
pub use qualify::quantity_qualifier;
use rows::{dedupe, emit, qualifier_counts, sram_bank_check};
use tables::{
    CLASS_GROUPS, CLASS_OVER_SECTION, DUAL_HOME_CLASSES, GROUPS, INSTANCE_CLASS, MIXED_SECTIONS,
    MODE_LEAF, NOT_CAPACITY, PROSE_GROUP, SECTION_GROUP_DIRECT, SECTION_PATTERNS, SECURITY_VOCAB,
    SIZING_UNITS, STANDALONE_LEAF_CLASSES, TITLE_NUMERAL,
};
pub use typed::typed_facts;

use crate::ExtractError;

/// `key_features_grid.SCHEMA`.
pub const SCHEMA: &str = "harness.electronics-key-features-grid.v1";

/// `key_features_grid._GRID_MAX_LEN`.
const GRID_MAX_LEN: usize = 72;

/// `key_features_grid.build_grid` — the ten-group grid from a family-document
/// record. Returns the same dict shape as the reference.
///
/// # Errors
///
/// [`ExtractError::Malformed`] when a key the reference indexes directly
/// (`record["_meta"]`, `feature["verbatim"]`, …) is absent or of the wrong
/// shape.
///
/// # Lints
///
/// Long because `build_grid` is one linear six-section pipeline in the
/// reference; splitting it would obscure the ported order (sections 1–6 and
/// the dedupe/qualifier tail run strictly in the reference's order). Bounded
/// here and nowhere else.
#[allow(clippy::too_many_lines)]
pub fn build_grid(
    record: &Value,
    vendor_by_sha: Option<&BTreeMap<String, String>>,
) -> Result<Value, ExtractError> {
    fn req<'a>(v: Option<&'a Value>, what: &str) -> Result<&'a Value, ExtractError> {
        v.ok_or_else(|| ExtractError::Malformed(format!("missing {what}")))
    }
    let meta = req(record.get("_meta"), "_meta")?;
    let identity = req(record.get("identity"), "identity")?;
    let source_path = req(meta.get("source_path"), "_meta.source_path")?
        .as_str()
        .unwrap_or_default();
    let artifact = source_path.rsplit('/').next().unwrap_or(source_path);
    let scope = scope_as_printed(identity)?;
    let lines = req(identity.get("lines_covered"), "identity.lines_covered")?;
    let group_tokens = lines.get("group_tokens").and_then(Value::as_array);
    let wildcards = req(lines.get("wildcards"), "lines_covered.wildcards")?;
    let parts = req(lines.get("parts"), "lines_covered.parts")?;
    let grain = if group_tokens.is_some_and(|t| !t.is_empty())
        || !arr_empty(wildcards)
        || !arr_empty(parts)
    {
        "series"
    } else {
        "family"
    };
    let mut vendor = normalize_vendor(meta.get("vendor").and_then(Value::as_str));
    let mut vendor_basis = vendor.is_some().then_some("document");
    if vendor.is_none() {
        let digest = meta
            .get("document_sha256")
            .and_then(Value::as_str)
            .unwrap_or("");
        vendor = vendor_by_sha
            .and_then(|m| m.get(digest))
            .and_then(|v| normalize_vendor(Some(v)));
        vendor_basis = vendor.is_some().then_some("inventory");
    }
    if vendor.is_none() {
        vendor = infer_vendor(
            identity.get("document_id").and_then(Value::as_str),
            &scope,
            artifact,
        );
        vendor_basis = vendor.is_some().then_some("inferred");
    }
    let mut base = Map::new();
    base.insert(
        "vendor".to_string(),
        match &vendor {
            Some(v) => json!(v),
            None => Value::Null,
        },
    );
    base.insert(
        "vendor_basis".to_string(),
        match vendor_basis {
            Some(v) => json!(v),
            None => Value::Null,
        },
    );
    base.insert("grain".to_string(), json!(grain));
    base.insert("scope_as_printed".to_string(), json!(scope));
    base.insert("source_artifact".to_string(), json!(artifact));
    base.insert(
        "document_sha256".to_string(),
        req(meta.get("document_sha256"), "_meta.document_sha256")?.clone(),
    );

    let empty_map = Map::new();
    let empty_vec: Vec<Value> = Vec::new();
    let peripheral_classes = record
        .get("chapters")
        .and_then(|c| c.get("peripheral_classes"))
        .and_then(Value::as_object)
        .unwrap_or(&empty_map);

    // Chapter titles: first level-1 entry per class, numeral prefix stripped.
    let mut chapter_titles: BTreeMap<String, String> = BTreeMap::new();
    for (cls, entries) in peripheral_classes {
        let entries = entries.as_array().unwrap_or(&empty_vec);
        for entry in entries {
            if entry.get("level").and_then(Value::as_i64) == Some(1) {
                let title = entry.get("title").and_then(Value::as_str).unwrap_or("");
                chapter_titles
                    .entry(cls.clone())
                    .or_insert_with(|| sub_all(TITLE_NUMERAL, title, ""));
                break;
            }
        }
    }

    let mut rows: Vec<Value> = Vec::new();

    // 1. Instance counts: the display ("SCI x6"), count as integer.
    let peripheral_instances = record
        .get("peripheral_instances")
        .and_then(Value::as_object)
        .unwrap_or(&empty_map);
    for (icls, data) in peripheral_instances {
        let instances = req(
            data.get("instances"),
            &format!("peripheral_instances.{icls}.instances"),
        )?
        .as_array()
        .ok_or_else(|| ExtractError::Malformed("instances not a list".to_string()))?;
        let strong: Vec<&Value> = instances
            .iter()
            .filter(|i| i.get("weak") != Some(&json!(true)))
            .collect();
        let Some((pcls, group_label)) = instance_class(icls) else {
            continue;
        };
        if strong.is_empty() {
            continue;
        }
        let names: Vec<String> = strong
            .iter()
            .filter_map(|i| i.get("instance").and_then(Value::as_str))
            .map(ToString::to_string)
            .collect();
        let stem = vendor_stem(&names, icls);
        // The chapter title is the label only when it names this stem (SPI's
        // chapter is not I2S's label; USART's chapter is not UART's).
        let label = match chapter_titles.get(pcls) {
            Some(t) if !t.is_empty() => {
                let probe = stem.split(" / ").next().unwrap_or(&stem);
                let pat = format!("(?<![A-Za-z]){}(?![A-Za-z])", regex_escape(probe));
                if is_match(&pat, t) {
                    t.clone()
                } else {
                    stem.clone()
                }
            }
            _ => stem.clone(),
        };
        for group in class_groups(pcls) {
            let mut flags = Map::new();
            flags.insert("group_label".to_string(), json!(group_label));
            flags.insert("instance_names".to_string(), json!(names));
            let weak: Vec<String> = instances
                .iter()
                .filter(|i| i.get("weak") == Some(&json!(true)))
                .filter_map(|i| i.get("instance").and_then(Value::as_str))
                .take(16)
                .map(ToString::to_string)
                .collect();
            flags.insert("weak_instance_names".to_string(), json!(weak));
            let pages: Vec<i64> = strong
                .iter()
                .filter_map(|i| i.get("first_page"))
                .filter_map(num_as_i64)
                .collect();
            rows.push(emit(
                &base,
                group,
                Some(pcls),
                &label,
                &pages,
                &names.join(", "),
                "grid",
                Some(json!(strong.len())),
                Value::Null,
                Value::Null,
                Value::Null,
                None,
                Some(flags),
            )?);
        }
    }

    // 2. Cover features: the vendor's own short list (family data sheets).
    let empty_features = Vec::new();
    let features = record
        .get("features")
        .and_then(Value::as_array)
        .unwrap_or(&empty_features);
    let has_features_table = features
        .iter()
        .any(|f| f.get("source") == Some(&json!("features_table")));
    for feature in features {
        let text = req(feature.get("verbatim"), "feature.verbatim")?;
        let text = text.as_str().unwrap_or_default();
        let label = feature
            .get("label")
            .and_then(Value::as_str)
            .unwrap_or(text)
            .to_string();
        let classes = feature
            .get("classes")
            .and_then(Value::as_array)
            .unwrap_or(&empty_vec);
        let section = feature.get("vendor_section").and_then(Value::as_str);
        // `_SECTION_WORDS` is an empty dict in the reference: the section
        // vocabulary table was never populated. Ported as always-None.
        let section_class = feature.get("section_class").and_then(Value::as_str);
        let numbers = feature
            .get("numbers")
            .and_then(Value::as_array)
            .unwrap_or(&empty_vec);
        let (value, unit) = single_number(numbers, &label);
        let cls = classes.first().and_then(Value::as_str);
        let section_key = norm_key(section);
        let section_group = section_group(&section_key);
        let mut bullet_groups: BTreeSet<String> = BTreeSet::new();
        for c in classes.iter().filter_map(Value::as_str) {
            for g in class_groups(c) {
                bullet_groups.insert((*g).to_string());
            }
        }
        let table_row = feature.get("source") == Some(&json!("features_table"));
        let mut groups: BTreeSet<String> = BTreeSet::new();
        // The vendor filed it under this heading; that is the group. A bullet
        // whose own class also belongs elsewhere keeps both homes.
        if let Some(sg) = section_group {
            let class_wins = table_row && cls.is_some_and(|c| CLASS_OVER_SECTION.contains(&c));
            if !MIXED_SECTIONS.contains(&section_key.as_str()) && !class_wins {
                groups.insert(sg.to_string());
                if cls.is_some_and(|c| DUAL_HOME_CLASSES.contains(&c)) {
                    groups.extend(bullet_groups.iter().cloned());
                }
            }
        }
        if groups.is_empty() && !bullet_groups.is_empty() {
            groups = bullet_groups;
        } else if groups.is_empty() {
            if let Some(sc) = section_class {
                for g in class_groups(sc) {
                    groups.insert((*g).to_string());
                }
            } else if let Some(sg) = section_group {
                groups.insert(sg.to_string());
            }
        }
        if groups.is_empty() && package_or_temp(text) {
            groups.insert("io_package_environment".to_string());
        }
        if groups.is_empty() && power_fact(text) {
            groups.insert("power_clock_reset".to_string());
        }
        let cls_owned = cls.or(section_class).map(ToString::to_string);
        // Sub-bullets are detail under their parent: below the grid — except a
        // short security-function line under a security engine, and a
        // sub-bullet that names a peripheral in its own right.
        let security_leaf = groups.contains("security_safety_identity")
            && label.chars().count() <= 48
            && SECURITY_VOCAB.iter().any(|(_, p)| is_match(p, &label));
        let standalone_leaf = cls_owned
            .as_deref()
            .is_some_and(|c| STANDALONE_LEAF_CLASSES.contains(&c))
            && label.chars().count() <= 48
            && !is_match(MODE_LEAF, &label);
        let level_one = feature.get("level").is_none_or(|l| l == &json!(1));
        let mut tier = if grid_eligible(&label) && (level_one || security_leaf || standalone_leaf) {
            "grid"
        } else {
            "below_grid"
        };
        // A document with a features table (TI) summarises the family there;
        // from its long "Features" section only a counted or sized line is a
        // grid fact.
        if has_features_table && feature.get("source") == Some(&json!("features_continuation")) {
            let counted = feature
                .get("count")
                .is_some_and(|c| c != &Value::Null && c != &json!(false));
            if !(counted || (value != Value::Null && unit.is_some())) {
                tier = "below_grid";
            }
        }
        let receipt = req(feature.get("receipt"), "feature.receipt")?;
        let page =
            num_as_i64(req(receipt.get("page"), "feature.receipt.page")?).unwrap_or_default();
        let mut sorted_groups: Vec<&String> = groups.iter().collect();
        sorted_groups.sort();
        for group in sorted_groups {
            let flags = feature
                .get("parent")
                .filter(|p| **p != Value::Null && **p != json!(""))
                .map(|p| {
                    let mut f = Map::new();
                    f.insert("parent".to_string(), p.clone());
                    f
                });
            rows.push(emit(
                &base,
                group,
                cls_owned.as_deref(),
                &label,
                &[page],
                text,
                tier,
                feature.get("count").cloned(),
                value.clone(),
                unit.clone().unwrap_or(Value::Null),
                feature
                    .get("qualifier_verbatim")
                    .cloned()
                    .unwrap_or(Value::Null),
                Some(section.unwrap_or("features")),
                flags,
            )?);
        }
    }

    // 2b. Vendor section headings that are facts themselves.
    let mut seen_sections: Vec<String> = Vec::new();
    for feature in features {
        let Some(section) = feature.get("vendor_section").and_then(Value::as_str) else {
            continue;
        };
        if section.is_empty()
            || SECTION_GROUP_DIRECT
                .iter()
                .any(|(k, _)| *k == norm_key(Some(section)))
            || seen_sections.iter().any(|s| s == section)
        {
            continue;
        }
        seen_sections.push(section.to_string());
        let cls = feature.get("section_class").and_then(Value::as_str);
        let page = feature
            .get("receipt")
            .and_then(|r| r.get("page"))
            .and_then(num_as_i64)
            .unwrap_or_default();
        for group in class_groups(cls.unwrap_or("")) {
            rows.push(emit(
                &base,
                group,
                cls,
                section,
                &[page],
                section,
                if grid_eligible(section) {
                    "grid"
                } else {
                    "below_grid"
                },
                None,
                Value::Null,
                Value::Null,
                Value::Null,
                Some("features_heading"),
                None,
            )?);
        }
    }

    // 3. Chapter "main features" bullets: family detail, below the grid
    //    except short capacity/speed facts with a sizing unit.
    for feature in record
        .get("chapter_features")
        .and_then(Value::as_array)
        .unwrap_or(&empty_vec)
    {
        let text = req(feature.get("verbatim"), "chapter_feature.verbatim")?;
        let text = text.as_str().unwrap_or_default();
        let cls = feature.get("chapter_class").and_then(Value::as_str);
        let groups = class_groups(cls.unwrap_or(""));
        if groups.is_empty() {
            continue;
        }
        let numbers = feature
            .get("numbers")
            .and_then(Value::as_array)
            .unwrap_or(&empty_vec);
        let (value, unit) = single_number(numbers, "");
        let sizing = unit.as_ref().is_some_and(|u| {
            let u = u.as_str().unwrap_or_default().to_lowercase();
            SIZING_UNITS.contains(&u.trim_end_matches('s'))
        });
        let tier = if grid_eligible(text)
            && text.chars().count() <= 56
            && sizing
            && matches!(cls, Some("flash" | "sram" | "memory_map" | "cpu_core"))
            && !is_match(NOT_CAPACITY, text)
        {
            "grid"
        } else {
            "below_grid"
        };
        let page = feature
            .get("receipt")
            .and_then(|r| r.get("page"))
            .and_then(num_as_i64)
            .unwrap_or_default();
        for group in groups {
            rows.push(emit(
                &base,
                group,
                cls,
                text,
                &[page],
                text,
                tier,
                None,
                value.clone(),
                unit.clone().unwrap_or(Value::Null),
                feature
                    .get("qualifier_verbatim")
                    .cloned()
                    .unwrap_or(Value::Null),
                feature.get("section").and_then(Value::as_str),
                None,
            )?);
        }
    }

    // 4. Memory regions with a size (one row per distinct label).
    let mut seen_memory: BTreeSet<String> = BTreeSet::new();
    for row in record
        .get("memory")
        .and_then(Value::as_array)
        .unwrap_or(&empty_vec)
    {
        let text = req(row.get("verbatim"), "memory.verbatim")?;
        let text = text.as_str().unwrap_or_default();
        if !seen_memory.insert(text.to_lowercase()) {
            continue;
        }
        // Sub-kilobyte numbers are programming/erase granularity, ECC word
        // sizes or note text, never a family memory capacity.
        let size_kb = req(row.get("size_kb"), "memory.size_kb")?.clone();
        let capacity = size_kb.as_f64().unwrap_or_default() >= 1.0 && !is_match(NOT_CAPACITY, text);
        let page = row
            .get("receipt")
            .and_then(|r| r.get("page"))
            .and_then(num_as_i64)
            .unwrap_or_default();
        let cls = if is_match(r"(?i)flash|program memory|otp", text) {
            "flash"
        } else {
            "sram"
        };
        rows.push(emit(
            &base,
            "memory",
            Some(cls),
            text,
            &[page],
            text,
            if capacity && grid_eligible(text) {
                "grid"
            } else {
                "below_grid"
            },
            None,
            size_kb,
            json!("KB"),
            row.get("qualifier_verbatim")
                .cloned()
                .unwrap_or(Value::Null),
            Some("memory"),
            None,
        )?);
    }

    // 5. Prose facts from the opening pages: core, max frequency, supply,
    //    temperature, packages.
    for fact in record
        .get("prose_facts")
        .and_then(Value::as_array)
        .unwrap_or(&empty_vec)
    {
        let kind = req(fact.get("kind"), "prose_fact.kind")?;
        let kind = kind.as_str().unwrap_or_default();
        let Some((group, cls)) = prose_group(kind) else {
            return Err(ExtractError::Malformed(format!(
                "unknown prose kind {kind}"
            )));
        };
        let verbatim = req(fact.get("verbatim"), "prose_fact.verbatim")?;
        let verbatim = verbatim.as_str().unwrap_or_default();
        let mut label = verbatim.to_string();
        let mut flags = Map::new();
        let mut value = fact.get("value").cloned().unwrap_or(Value::Null);
        if kind == "packages" {
            let listed = req(fact.get("value"), "prose_fact.value")?;
            label = listed
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(Value::as_str)
                        .collect::<Vec<_>>()
                        .join(", ")
                })
                .unwrap_or_default();
            flags.insert("packages".to_string(), listed.clone());
            value = Value::Null;
        }
        if let Some(context) = fact
            .get("context")
            .filter(|c| **c != Value::Null && **c != json!(""))
        {
            flags.insert("context".to_string(), context.clone());
        }
        let page = fact
            .get("receipt")
            .and_then(|r| r.get("page"))
            .and_then(num_as_i64)
            .unwrap_or_default();
        rows.push(emit(
            &base,
            group,
            Some(cls),
            &label,
            &[page],
            verbatim,
            if label.chars().count() <= GRID_MAX_LEN {
                "grid"
            } else {
                "below_grid"
            },
            None,
            value,
            fact.get("unit").cloned().unwrap_or(Value::Null),
            fact.get("qualifier_verbatim")
                .cloned()
                .unwrap_or(Value::Null),
            Some(kind),
            Some(flags),
        )?);
    }

    // 5b. Package-qualified I/O counts. One row per (pin count, package);
    //     never a scalar, never expanded to members; `varies_by_part` forced.
    for row in record
        .get("io_by_package")
        .and_then(Value::as_array)
        .unwrap_or(&empty_vec)
    {
        let pattern = req(row.get("pattern"), "io_by_package.pattern")?;
        let counted = pattern == &json!("pin_table_count");
        let io_count = req(row.get("io_count"), "io_by_package.io_count")?.clone();
        let pin_count = req(row.get("pin_count"), "io_by_package.pin_count")?.clone();
        let package = req(row.get("package"), "io_by_package.package")?.clone();
        let label = format!(
            "{} {} ({}-pin {})",
            py_str(&io_count),
            if counted { "port pins listed" } else { "I/O" },
            py_str(&pin_count),
            py_str(&package)
        );
        let receipt = req(row.get("receipt"), "io_by_package.receipt")?;
        let pages: Vec<i64> = receipt
            .get("pages")
            .and_then(Value::as_array)
            .filter(|p| !p.is_empty())
            .map_or_else(
                || {
                    receipt
                        .get("page")
                        .and_then(num_as_i64)
                        .into_iter()
                        .collect()
                },
                |p| p.iter().filter_map(num_as_i64).collect(),
            );
        let verbatim = row
            .get("verbatim")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let mut pq = Map::new();
        pq.insert("pin_count".to_string(), pin_count);
        pq.insert("package".to_string(), package);
        let mut flags = Map::new();
        flags.insert("package_qualifier".to_string(), Value::Object(pq));
        flags.insert("varies_by_part".to_string(), json!(true));
        flags.insert("pattern".to_string(), pattern.clone());
        flags.insert(
            "quantity_qualifier".to_string(),
            json!(if counted {
                "port_pins_listed"
            } else {
                "stated"
            }),
        );
        rows.push(emit(
            &base,
            "io_package_environment",
            Some("gpio"),
            &label,
            &pages,
            verbatim,
            "grid",
            None,
            io_count,
            json!("I/O"),
            Value::Null,
            Some("io_by_package"),
            Some(flags),
        )?);
    }

    // 6. Chapter presence: asserted present, with location. Never absence.
    let mut presence: Vec<Value> = Vec::new();
    for (cls, entries) in peripheral_classes {
        let entries = entries.as_array().unwrap_or(&empty_vec);
        let chapters: Vec<Value> = entries
            .iter()
            .take(6)
            .map(|e| {
                json!({
                    "title": e.get("title").cloned().unwrap_or(Value::Null),
                    "page": e.get("page").cloned().unwrap_or(Value::Null),
                })
            })
            .collect();
        presence.push(json!({
            "peripheral_class": cls,
            "presence": "asserted_present",
            "evidence": "chapter",
            "chapters": chapters,
            "groups": class_groups(cls),
        }));
        for group in class_groups(cls) {
            let top = entries.first().ok_or_else(|| {
                ExtractError::Malformed(format!("empty chapter entries for {cls}"))
            })?;
            let title = top.get("title").and_then(Value::as_str).unwrap_or("");
            let pages: Vec<i64> = entries
                .iter()
                .take(6)
                .filter_map(|e| e.get("page"))
                .filter_map(num_as_i64)
                .collect();
            rows.push(emit(
                &base,
                group,
                Some(cls),
                title,
                &pages,
                title,
                "below_grid",
                None,
                Value::Null,
                Value::Null,
                Value::Null,
                Some("chapter"),
                None,
            )?);
        }
    }
    let mut observed: BTreeSet<String> = presence
        .iter()
        .filter_map(|p| {
            p.get("peripheral_class")
                .and_then(Value::as_str)
                .map(ToString::to_string)
        })
        .collect();
    for (k, v) in peripheral_instances {
        if let Some((pcls, _)) = instance_class(k) {
            // Python truthiness on `count_asserted`: the reference stores
            // counts here (2, 1, 0), not booleans — `0` is falsy, `1` is not.
            if v.get("count_asserted").is_some_and(py_truthy) {
                observed.insert(pcls.to_string());
            }
        }
    }
    let all_classes: BTreeSet<String> =
        CLASS_GROUPS.iter().map(|(k, _)| (*k).to_string()).collect();
    let not_observed: Vec<String> = all_classes.difference(&observed).cloned().collect();

    let mut rows = dedupe(rows)?;
    for row in &mut rows {
        let existing = row
            .get("quantity_qualifier")
            .filter(|q| **q != Value::Null && **q != json!(""))
            .cloned();
        let qq = existing.unwrap_or_else(|| match quantity_qualifier(row) {
            Some(q) => json!(q),
            None => Value::Null,
        });
        set_key(row, "quantity_qualifier", qq);
        let facts = typed_facts(row);
        if facts.as_array().is_some_and(|f| !f.is_empty()) {
            set_key(row, "typed", facts);
        }
    }
    let sram_check = sram_bank_check(&mut rows)?;
    let mut typed_by_group: BTreeMap<String, u64> = BTreeMap::new();
    let mut typed_kinds: BTreeMap<String, u64> = BTreeMap::new();
    for row in &rows {
        let group = row
            .get("group")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        for fact in row
            .get("typed")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            *typed_by_group.entry(group.clone()).or_insert(0) += 1;
            if let Some(kind) = fact.get("kind").and_then(Value::as_str) {
                *typed_kinds.entry(kind.to_string()).or_insert(0) += 1;
            }
        }
    }
    let grid_rows: Vec<&Value> = rows
        .iter()
        .filter(|r| r.get("tier") == Some(&json!("grid")))
        .collect();
    let mut populated: BTreeMap<String, u64> = BTreeMap::new();
    for row in &grid_rows {
        let g = row
            .get("group")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        *populated.entry(g).or_insert(0) += 1;
    }
    let groups_json: Vec<Value> = GROUPS
        .iter()
        .map(|(k, l)| {
            json!({
                "key": k,
                "label": l,
                "grid_rows": populated.get(*k).copied().unwrap_or(0),
            })
        })
        .collect();
    let mut meta_out = base.clone();
    for (src, dst) in [
        ("document_id", "document_id"),
        ("revision", "revision"),
        ("title_verbatim", "title_verbatim"),
    ] {
        meta_out.insert(
            dst.to_string(),
            identity.get(src).cloned().unwrap_or(Value::Null),
        );
    }
    for src in ["page_count", "extracted_at"] {
        meta_out.insert(
            src.to_string(),
            meta.get(src).cloned().unwrap_or(Value::Null),
        );
    }
    meta_out.insert("models_used".to_string(), json!([]));
    let mut flags_out = Map::new();
    flags_out.insert(
        "npu_group_undecided".to_string(),
        json!(observed.contains("npu")),
    );
    for (k, v) in sram_check.as_object().cloned().unwrap_or_default() {
        flags_out.insert(k, v);
    }
    let qq_counts = qualifier_counts(&grid_rows);
    Ok(json!({
        "schema": SCHEMA,
        "_meta": Value::Object(meta_out),
        "groups": groups_json,
        "groups_populated": populated.len(),
        "meets_six_of_ten": populated.len() >= 6,
        "rows": rows,
        "class_presence": presence,
        "not_observed": not_observed,
        "flags": Value::Object(flags_out),
        "quantity_qualifiers": Value::Object(qq_counts.into_iter().map(|(k, v)| (k, json!(v))).collect()),
        "typed_facts_by_group": Value::Object(typed_by_group.into_iter().map(|(k, v)| (k, json!(v))).collect()),
        "typed_fact_kinds": Value::Object(typed_kinds.into_iter().map(|(k, v)| (k, json!(v))).collect()),
    }))
}

/// `_single_number` — the one scalar-sized number of a numbers list, if any.
#[must_use]
pub fn single_number(numbers: &[Value], text: &str) -> (Value, Option<Value>) {
    let typed: Vec<&Value> = numbers
        .iter()
        .filter(|n| {
            n.get("unit")
                .is_some_and(|u| *u != Value::Null && *u != json!(""))
        })
        .collect();
    if !text.is_empty()
        && (is_match(tables::RANGE, text)
            || is_match(tables::ALT_LIST, text)
            || is_match(tables::COMMA_LIST, text))
    {
        // "2.0 to 3.6 V", "16 or 32 Kbytes": a range or list, not a scalar.
        return (Value::Null, None);
    }
    if let [only] = typed.as_slice() {
        if numbers.len() <= 2 {
            return pick(only);
        }
        // "8 KB data flash memory (100,000 P/E cycles)": the sized number
        // leads and the unitless numbers after it are detail.
        if numbers.first().is_some_and(|f| std::ptr::eq(f, *only)) {
            return pick(only);
        }
    }
    (Value::Null, None)
}

fn pick(number: &Value) -> (Value, Option<Value>) {
    (
        number.get("value").cloned().unwrap_or(Value::Null),
        number.get("unit").cloned(),
    )
}

/// `_scope_as_printed` — the bind-to-catalogue token.
///
/// # Errors
///
/// [`ExtractError::Malformed`] when `lines_covered.wildcards` / `.parts` —
/// which the reference indexes directly — are absent.
pub fn scope_as_printed(identity: &Value) -> Result<String, ExtractError> {
    let lines = identity
        .get("lines_covered")
        .ok_or_else(|| ExtractError::Malformed("identity.lines_covered".to_string()))?;
    let as_list = |k: &str| -> Vec<String> {
        lines
            .get(k)
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(ToString::to_string)
                    .collect()
            })
            .unwrap_or_default()
    };
    let group_tokens = lines
        .get("group_tokens")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .map(ToString::to_string)
                .collect::<Vec<String>>()
        })
        .unwrap_or_default();
    let wildcards = lines
        .get("wildcards")
        .and_then(Value::as_array)
        .ok_or_else(|| ExtractError::Malformed("lines_covered.wildcards".to_string()))?;
    let parts = lines
        .get("parts")
        .and_then(Value::as_array)
        .ok_or_else(|| ExtractError::Malformed("lines_covered.parts".to_string()))?;
    let wildcards: Vec<String> = wildcards
        .iter()
        .filter_map(Value::as_str)
        .map(ToString::to_string)
        .collect();
    let parts: Vec<String> = parts
        .iter()
        .filter_map(Value::as_str)
        .map(ToString::to_string)
        .collect();
    for candidates in [&group_tokens[..], &wildcards[..], &parts[..]] {
        let good: Vec<String> = candidates
            .iter()
            .filter(|c| !non_scope(c))
            .cloned()
            .collect();
        if !good.is_empty() {
            return Ok(good.iter().take(8).cloned().collect::<Vec<_>>().join(", "));
        }
    }
    for phrase in as_list("family_phrases") {
        if !non_scope(&phrase) && is_match(r"\d", &phrase) {
            return Ok(phrase);
        }
    }
    let filenames = as_list("filename_tokens");
    if let Some(first) = filenames.first() {
        return Ok(first.clone());
    }
    let title = identity
        .get("pdf_title")
        .and_then(Value::as_str)
        .or_else(|| identity.get("title_verbatim").and_then(Value::as_str))
        .unwrap_or("");
    Ok(if non_scope(title.trim()) {
        String::new()
    } else {
        title.to_string()
    })
}

/// Python truthiness for the JSON shapes records carry.
fn py_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn section_group(section_key: &str) -> Option<&'static str> {
    if let Some((_, group)) = SECTION_GROUP_DIRECT.iter().find(|(k, _)| *k == section_key) {
        return Some(group);
    }
    SECTION_PATTERNS
        .iter()
        .find(|(p, _)| is_match(p, section_key))
        .map(|(_, g)| *g)
}

fn class_groups(class: &str) -> &'static [&'static str] {
    CLASS_GROUPS
        .iter()
        .find(|(k, _)| *k == class)
        .map_or(&[], |(_, g)| g)
}

fn instance_class(icls: &str) -> Option<(&'static str, &'static str)> {
    INSTANCE_CLASS
        .iter()
        .find(|(k, _, _)| *k == icls)
        .map(|(_, p, l)| (*p, *l))
}

fn prose_group(kind: &str) -> Option<(&'static str, &'static str)> {
    PROSE_GROUP
        .iter()
        .find(|(k, _, _)| *k == kind)
        .map(|(_, g, c)| (*g, *c))
}

#[cfg(test)]
// Test module: asserts are the point here; unwraps on fixture data are the
// documented allowance from HANDOFF.md ("Test modules may carry a documented
// #[allow]").
#[allow(clippy::unwrap_used, clippy::expect_used, clippy::indexing_slicing)]
mod tests {
    use super::helpers::{label_key, varies_by_part};
    use super::*;

    fn sample() -> Value {
        json!({
            "_meta": {
                "source_path": "/x/STM32F103C4_Datasheet.pdf",
                "document_sha256": "a".repeat(64),
                "vendor": "st.com",
                "page_count": 8,
                "extracted_at": "2026-09-09T00:00:00+00:00",
            },
            "identity": {
                "document_id": "STM32F103C4",
                "revision": "Rev 3.1",
                "title_verbatim": "STM32F103C4",
                "lines_covered": {"group_tokens": ["STM32F103C4"], "wildcards": [], "parts": []},
            },
            "peripheral_instances": {
                "TIM": {"instances": [
                    {"instance": "TIM1", "weak": false, "first_page": 3},
                    {"instance": "TIM2", "weak": false, "first_page": 3},
                ], "count_asserted": true},
            },
            "features": [
                {"verbatim": "Up to 72 MHz CPU", "label": "Up to 72 MHz CPU",
                 "classes": ["cpu_core"], "vendor_section": "Processing",
                 "numbers": [{"value": 72, "unit": "MHz"}], "level": 1,
                 "receipt": {"page": 2}, "source": "features"},
            ],
            "chapter_features": [],
            "memory": [],
            "prose_facts": [],
            "io_by_package": [],
            "chapters": {"peripheral_classes": {"timer": [
                {"title": "16-bit timers", "page": 3, "level": 1},
            ]}},
        })
    }

    /// Gate: a minimal record produces the ten-group scaffold and a grid row.
    /// Failure condition (R1): missing `groups`, or the CPU feature not
    /// on the grid tier in the processing group.
    #[test]
    fn build_grid_emits_scaffold_and_rows() {
        let grid = build_grid(&sample(), None).unwrap();
        assert_eq!(grid["schema"], json!(SCHEMA));
        assert_eq!(grid["groups"].as_array().unwrap().len(), 10);
        let rows = grid["rows"].as_array().unwrap();
        let cpu: Vec<&Value> = rows
            .iter()
            .filter(|r| r["label"] == json!("Up to 72 MHz CPU"))
            .collect();
        assert_eq!(cpu.len(), 1);
        assert_eq!(cpu[0]["tier"], json!("grid"));
        assert_eq!(cpu[0]["group"], json!("processing"));
    }

    /// Negative control (R2): a >72-char label must NOT be grid tier. If
    /// `grid_eligible` is not consulted, this stays green while the gate is
    /// meaningless.
    #[test]
    fn negative_control_long_label_stays_below_grid() {
        assert!(!grid_eligible(&"x".repeat(73)));
        assert!(grid_eligible(&"x".repeat(72)));
    }

    /// Negative control: sentence openings and trailing punctuation are not
    /// grid facts.
    #[test]
    fn negative_control_sentence_fragments_stay_below_grid() {
        assert!(!grid_eligible("The device supports DMA"));
        assert!(!grid_eligible("up to 8 channels:"));
        assert!(!grid_eligible("ends with a dot."));
    }

    /// Gate: `varies_by_part` fires on ranges/lists/hedges, not on scalars.
    #[test]
    fn varies_by_part_shapes() {
        assert!(varies_by_part("2.0 to 3.6 V"));
        assert!(varies_by_part("16 or 32 Kbytes"));
        assert!(varies_by_part("depending on the package"));
        assert!(!varies_by_part("72 MHz CPU"));
    }

    /// Negative control: `label_key` keeps "timer" distinct from "timers"
    /// (only acronym plurals collapse).
    #[test]
    fn negative_control_acronym_plural_only() {
        assert_eq!(label_key("UARTs"), label_key("UART"));
        assert_ne!(label_key("timers"), label_key("timer"));
    }
}
