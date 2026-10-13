//! L0 substrate: what every document is, and where things are in it
//! (`harness/electronics/l0.py`, ported 1:1).
//!
//! One pass, deterministic, no model: classify the document (datasheet,
//! errata, PCN, BOM, reference design, ...), map its sections to extraction
//! lanes via the TOC, and flag the pages that text cannot reach (text-poor,
//! image/drawing-heavy) so the vision backlog falls out of the substrate
//! instead of guesswork. Extension classes beat every content rule — a zip
//! is a container whatever its bytes say, and `.bin` is deliberately
//! unclassifiable (fail closed). The images/drawings layer is PDF-layer:
//! per-page counts travel as captured inputs.

use std::collections::BTreeMap;

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::page_index::classify_section;

/// The L0 record schema id.
pub const L0_SCHEMA: &str = "harness.electronics-l0.v1";

/// Pages below this many characters are text-poor.
const MIN_TEXT_CHARS: usize = 120;

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn re(src: &str) -> Regex {
    Regex::new(src).expect("pattern is a literal from the reference")
}

/// Document class rules, first match wins (reference order).
const CLASS_RULES: &[(&str, &str)] = &[
    (
        "errata",
        r"\berrata\b|\badvisories\b|known\s+exceptions.{0,40}advisories|silicon\s+errata",
    ),
    (
        "pcn",
        r"product\s+change\s+notification|\bPCN\b\s*(number|date|\:)|change\s+notification",
    ),
    (
        "bom",
        r"bill\s+of\s+materials|\bBOM\b\s*[:)]|assembly\s+drawings?\s+and\s+bill",
    ),
    (
        "reference_design",
        r"evaluation\s+(board|module|kit)|\bEVM\b|reference\s+design|evaluation\s+users?\s+guide",
    ),
    (
        "reference_manual",
        r"\breference\s+manual\b|register\s+map|bit\s+field\s+(description|table)",
    ),
    (
        "user_manual",
        r"\busers?\s+(manual|guide)\b|programming\s+manual|software\s+designers?\s+guide",
    ),
    (
        "application_note",
        r"application\s+note|\bAN\s*[:#]?\s*\d{2,6}\b|design\s+guide|application\s+report",
    ),
    (
        "datasheet",
        r"\b(data\s*sheet|datasheet)\b|electrical\s+characteristics|absolute\s+maximum\s+ratings|recommended\s+operating\s+conditions",
    ),
    ("brochure", r"\bbrochure\b|\bflyer\b|\bnewsletter\b"),
    ("white_paper", r"\bwhite\s+paper\b|\btechnical\s+brief\b"),
    (
        "fact_sheet",
        r"\bfact\s*sheet\b|\bproduct\s+brief\b|\bselector\s+guide\b",
    ),
    (
        "package_drawing",
        r"package\s+(?:drawing|outline|dimensions?)|mechanical\s+(?:data|drawing)|land\s*pattern|\bPOD\b\s*[:#]",
    ),
    (
        "bonding_diagram",
        r"bond(?:ing)?\s+diagram|die\s+(?:pad|attach)\s+layout",
    ),
    (
        "pinout_diagram",
        r"pin(?:out)?\s+diagram|pin\s+assignment\s+diagram|terminal\s+assignment\s+diagram",
    ),
    ("design_note", r"\bdesign\s+note\b|\bDN\s*[:#]\s*\d"),
    (
        "conformance_report",
        r"conformance\s+(?:report|declaration)|declaration\s+of\s+conform",
    ),
    (
        "customer_drawing",
        r"customer\s+drawing|\bCD\s*[:#]\s*\d{4,}",
    ),
    (
        "eval_document",
        r"evaluation\s+(?:board|kit|module).{0,40}(?:schematic|bom|bill|gerber|test|user|guide|procedure)|\bEVM\b.{0,40}(?:schematic|bom|gerber|test|user|guide)",
    ),
    (
        "simulation_model",
        r"\b(P-?SPICE|TINA-?TI|SIMetrix|LTspice|IBIS)\b|\.(zip|lib|mod|ibs)\b|model\s+(?:file|kit|package)",
    ),
];

/// Filename hints, first match wins (reference order).
const FILENAME_HINTS: &[(&str, &str)] = &[
    ("errata", r"errata|sprz\d|slaz\d|_es_|-es\d|errataclarif"),
    ("pcn", r"pcn|change.?notification"),
    ("bom", r"bom|bill.?of.?materials"),
    (
        "reference_design",
        r"evm|eval|dev.?kit|refdesign|reference.?design|tidu|tidm",
    ),
    ("reference_manual", r"\brm\d{4}\b|reference.?manual"),
    ("user_manual", r"\bum\d{4}\b|users?.?(manual|guide)"),
    (
        "application_note",
        r"\ban\d{5,6}\b|app.?note|application.?note|slva|sluu|slyt",
    ),
    (
        "datasheet",
        r"datasheet|data.?sheet|\bds\d{6,7}\b|\bds7000\d+|slas|slfs|slyt",
    ),
];

/// Non-document extensions, classified before any content rule. `.bin` is
/// deliberately absent: a firmware blob is not an EDA model.
const EXTENSION_CLASSES: &[(&[&str], &str)] = &[
    (&["zip", "7z", "rar", "tar", "gz", "tgz"], "container"),
    (
        &[
            "png", "gif", "jpg", "jpeg", "tif", "tiff", "bmp", "svg", "webp",
        ],
        "image_only",
    ),
    (&["txt", "md", "log", "rep"], "sidecar_text"),
    (&["xlsx", "xls", "xlsm", "csv", "tsv"], "tabular_sheet"),
    (
        &[
            "s1p", "s2p", "s3p", "s4p", "snp", "sp", "spi", "sp3", "sp4", "lib", "mod", "ibs",
            "sin", "cir",
        ],
        "eda_model",
    ),
    (
        &[
            "gbr", "gpi", "art", "pho", "dri", "drl", "gto", "gbo", "gts", "gbs", "gtp", "gbp",
            "gko", "gm1", "gm2", "gtl", "gbl",
        ],
        "gerber",
    ),
    (
        &[
            "asy",
            "olb",
            "sxsch",
            "sxslb",
            "sch",
            "brd",
            "kicad_sch",
            "kicad_pcb",
            "pcb",
            "dsn",
            "net",
            "v",
            "vhd",
            "edf",
            "prj",
            "prjpcb",
        ],
        "design_file",
    ),
];

/// The manifest prior's class map (lowercased manifest value -> class).
const PRIOR_CLASS_MAP: &[(&str, &str)] = &[
    ("reference_design", "reference_design"),
    ("design_file", "design_file"),
    ("eval_schematic", "schematic"),
    ("eval_bom", "bom"),
    ("eval_gerber", "gerber"),
    ("bom", "bom"),
    ("user_manual", "user_manual"),
    ("application_note", "application_note"),
    ("errata", "errata"),
    ("datasheet", "datasheet"),
    ("fact_sheet", "fact_sheet"),
    ("reliability_report", "reliability_report"),
    ("product_brief", "product_brief"),
    ("pcn", "pcn"),
    ("simulation_model", "simulation_model"),
    ("reference_manual", "reference_manual"),
    ("package_drawing", "package_drawing"),
    ("white_paper", "white_paper"),
];

/// One page of the PDF layer's output for the substrate pass.
pub struct PageFacts {
    /// Page text (`get_text()`).
    pub text: String,
    /// `get_images(full=True)` count.
    pub images: usize,
    /// `get_drawings()` count, `None` when the call raised.
    pub drawings: Option<usize>,
}

/// The class a non-document extension forces, or `None` for documents.
#[must_use]
pub fn extension_class(filename: &str) -> Option<String> {
    let ext = match filename.rsplit_once('.') {
        Some((_, ext)) => ext.to_lowercase(),
        None => return None,
    };
    if ext.is_empty() {
        return None;
    }
    EXTENSION_CLASSES
        .iter()
        .find(|(exts, _)| exts.contains(&ext.as_str()))
        .map(|(_, label)| (*label).to_string())
}

/// The document class: extension first, then content rules, then filename
/// hints, else `unknown`.
#[must_use]
pub fn classify_document(front_text: &str, filename: &str, toc_titles: &str) -> String {
    if let Some(forced) = extension_class(filename) {
        return forced;
    }
    let front: String = front_text.chars().take(4000).collect();
    let toc: String = toc_titles.chars().take(2000).collect();
    let evidence = format!("{front}\n{toc}");
    for (label, pattern) in CLASS_RULES {
        if re(&format!("(?i){pattern}")).is_match(&evidence) {
            return (*label).to_string();
        }
    }
    for (label, pattern) in FILENAME_HINTS {
        if re(&format!("(?i){pattern}")).is_match(filename) {
            return (*label).to_string();
        }
    }
    "unknown".to_string()
}

/// What this document is and where the extractable parts are.
#[must_use]
#[allow(
    clippy::too_many_lines,
    clippy::indexing_slicing,
    reason = "faithful port of l0_record; the page walks index as the reference does"
)]
pub fn l0_record(
    pages: &[PageFacts],
    toc: &[Value],
    source_path: &str,
    manifest_prior: Option<&str>,
) -> Value {
    let page_count = pages.len();
    let front_text = if page_count > 0 {
        pages[0].text.as_str()
    } else {
        ""
    };
    let toc_titles: String = toc
        .iter()
        .filter(|item| item.as_array().is_some_and(|a| a.len() >= 2))
        .map(|item| match &item[1] {
            Value::String(t) => t.clone(),
            other => other.to_string(),
        })
        .collect::<Vec<_>>()
        .join(" ");
    let filename = source_path.rsplit('/').next().unwrap_or(source_path);
    let mut doc_class = classify_document(front_text, filename, &toc_titles);
    let mut class_source = "text".to_string();
    if doc_class == "unknown" {
        if let Some(prior) = manifest_prior.map(str::to_lowercase) {
            if let Some((_, mapped)) = PRIOR_CLASS_MAP.iter().find(|(key, _)| *key == prior) {
                doc_class = (*mapped).to_string();
                class_source = "manifest_prior".to_string();
            }
        }
    }

    let mut sections: Vec<Value> = Vec::new();
    let mut seen: std::collections::BTreeSet<(String, i64)> = std::collections::BTreeSet::new();
    let mut lane_counts: BTreeMap<String, i64> = BTreeMap::new();
    for entry in toc {
        let Some(row) = entry.as_array() else {
            continue;
        };
        // Python `for _, title, page in toc` unpacks exactly three.
        if row.len() != 3 {
            continue;
        }
        let (Some(title), Some(page)) = (row.get(1), row.get(2)) else {
            continue;
        };
        let title = match title {
            Value::String(t) => t.clone(),
            other => other.to_string(),
        };
        let page = match page {
            Value::Number(n) => n.as_i64().unwrap_or_default(),
            _ => 0,
        };
        if title.is_empty() || page < 1 || page > i64::try_from(page_count).unwrap_or(i64::MAX) {
            continue;
        }
        let key = (title.split_whitespace().collect::<Vec<_>>().join(" "), page);
        if seen.contains(&key) {
            continue;
        }
        seen.insert(key.clone());
        let lanes: Vec<String> = classify_section(&key.0);
        for lane in &lanes {
            *lane_counts.entry(lane.clone()).or_insert(0) += 1;
        }
        let mut section = Map::new();
        let clipped: String = key.0.chars().take(120).collect();
        section.insert("title".into(), json!(clipped));
        section.insert("page_1based".into(), json!(key.1));
        section.insert("lanes".into(), json!(lanes));
        sections.push(Value::Object(section));
    }

    let mut needs_ocr_pages: Vec<i64> = Vec::new();
    let mut vision_candidate_pages: Vec<i64> = Vec::new();
    let mut total_chars = 0usize;
    for (index, page) in pages.iter().enumerate() {
        total_chars += page.text.chars().count();
        let chars = page.text.trim().chars().count();
        let images = page.images;
        let drawings = if chars < MIN_TEXT_CHARS {
            page.drawings.unwrap_or(0)
        } else {
            0
        };
        let page_1based = i64::try_from(index + 1).unwrap_or(i64::MAX);
        if chars < 40 && (images > 0 || drawings > 0) {
            needs_ocr_pages.push(page_1based);
        } else if chars < MIN_TEXT_CHARS && (images + drawings) >= 2 {
            vision_candidate_pages.push(page_1based);
        }
    }
    let avg_chars = total_chars.checked_div(page_count).unwrap_or(0);
    let mut record = Map::new();
    record.insert("schema".into(), json!(L0_SCHEMA));
    record.insert("source_path".into(), json!(source_path));
    record.insert("doc_class".into(), json!(doc_class));
    record.insert("class_source".into(), json!(class_source));
    record.insert("page_count".into(), json!(page_count));
    record.insert("toc_sections".into(), json!(sections.len()));
    let capped: Vec<Value> = sections.into_iter().take(400).collect();
    record.insert("sections".into(), json!(capped));
    record.insert("lane_counts".into(), json!(lane_counts));
    let ocr_count = needs_ocr_pages.len();
    record.insert(
        "needs_ocr_pages".into(),
        json!(needs_ocr_pages.into_iter().take(200).collect::<Vec<_>>()),
    );
    record.insert("needs_ocr_count".into(), json!(ocr_count));
    let vision_count = vision_candidate_pages.len();
    record.insert(
        "vision_candidate_pages".into(),
        json!(vision_candidate_pages
            .into_iter()
            .take(400)
            .collect::<Vec<_>>()),
    );
    record.insert("vision_candidate_count".into(), json!(vision_count));
    record.insert("avg_page_chars".into(), json!(avg_chars));
    let quality = if avg_chars >= 800 {
        "good"
    } else if avg_chars >= 200 {
        "thin"
    } else {
        "scanned"
    };
    record.insert("text_quality".into(), json!(quality));
    Value::Object(record)
}
