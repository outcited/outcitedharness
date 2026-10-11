//! `dw-lessons` — the proposal gate's precheck.
//!
//! ```text
//! dw-lessons index                 # what the index knows
//! dw-lessons query "<text>"        # ranked lessons for free text
//! dw-lessons check "<proposal>"    # exit 1 when a known lesson applies
//! ```
//!
//! `check` is the whole point: a proposal gate that runs it cannot propose
//! an experiment that re-walks a known failure class. `index`/`query` exist
//! so a human can see what that means.
//!
//! # Lints
//!
//! A CLI `main` that parses three verbs and maps results to exit codes is
//! one linear sequence; splitting it would scatter the exit-code contract.

#![forbid(unsafe_code)]
#![deny(missing_docs)]
#![allow(clippy::expect_used, reason = "doc paths resolve or we exit loudly")]

use std::path::PathBuf;

use dw_lessons::{build_index, check, query, Lesson};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let (verb, rest) = match args.split_first() {
        Some((v, rest)) if matches!(v.as_str(), "index" | "query" | "check") => (v.clone(), rest),
        _ => {
            eprintln!("usage: dw-lessons <index|query|check> [text]");
            std::process::exit(2);
        }
    };
    let docs = find_docs();
    let read = |name: &str| -> String {
        std::fs::read_to_string(docs.join(name)).unwrap_or_else(|e| {
            eprintln!("dw-lessons: cannot read {}: {e}", docs.join(name).display());
            std::process::exit(3);
        })
    };
    let index = build_index(
        &read("DECISIONS.md"),
        &read("ASSURANCE.md"),
        &read("HANDOFF.md"),
    );

    match verb.as_str() {
        "index" => {
            for Lesson {
                id, source, title, ..
            } in &index
            {
                println!("{id:6} {source:16} {title}");
            }
        }
        "query" => {
            let text = rest.first().map_or("", String::as_str);
            let top = rest
                .iter()
                .position(|a| a == "--top")
                .and_then(|i| rest.get(i + 1))
                .and_then(|n| n.parse().ok())
                .unwrap_or(5usize);
            for m in query(&index, text, top) {
                println!("{:.3} {:6} {}", m.score, m.lesson.id, m.lesson.title);
            }
        }
        "check" => {
            let text = rest.first().map_or("", String::as_str);
            match check(&index, text) {
                Ok(()) => println!("clear: no known lesson applies"),
                Err(hits) => {
                    eprintln!(
                        "blocked: {} known lesson(s) apply — address or override:",
                        hits.len()
                    );
                    for m in &hits {
                        eprintln!(
                            "  {:.3} [{}] {} — {}",
                            m.score, m.lesson.id, m.lesson.title, m.lesson.source
                        );
                    }
                    std::process::exit(1);
                }
            }
        }
        _ => unreachable!("verb is matched above"),
    }
}

/// Walk up from the crate to `designwins/docs`.
fn find_docs() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    while !dir.join("docs/DECISIONS.md").is_file() {
        assert!(dir.pop(), "could not locate designwins/docs");
    }
    dir.join("docs")
}
