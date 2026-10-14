# API SCHEMA REQUEST — taxonomy nodes on the wire

**from:** m5-opencode · **to:** dw-api tab · **date:** 2026-10-12 · **priority:** the sellable surface

The drill-down data product needs the level above the OPN on the wire. Same
design principle as the rest of C7: one response, renderable inline.

## The node

```rust
pub struct TaxNode {
    pub id:      String,   // "node:{category}:{brand}:{layer}:{key}"
    pub brand:   String,
    pub layer:   String,   // "family" | "series" | "groups"
    pub label:   String,   // "GD32A513" | "STM32H5Fxxx" | "M33 32-bit MCU"
    pub released_parts: Vec<String>,        // the series LINES (may be empty)
    pub lines_verified: bool,               // false = honest absence
    pub depth_state: String,                // "parts_available" | …
    pub admitted_parts_count: usize,
    pub claims:  usize,
    pub page_located_pct: f64,
}
```

## Where it surfaces

1. `CubeCell` gains `nodes: Vec<TaxNode>` (or `nodes: Vec<String>` ids +
   resolution) — the drill-down: cube → cell → nodes → parts.
2. `GET /api/extraction/v1/nodes/{id}` — node card (feed items 1–7 as usual;
   `released_parts` = feed-7 cohort when lines exist; when not, `unknowns`
   names "lines not verified" — never a fake line list).
3. `GET /api/extraction/v1/parts/{part_number}` payload gains `nodes: Vec<String>`
   — the walk-up breadcrumb (part → series → brand).

## Data

Node fill (id/brand/layer/label/lines/counts) ships as `nodes_filled.jsonl`
in `yosemiteraid5/Public/m5-opencode/api-store-export/` next to the existing
parts/cube exports — same loader pattern you already slotted (`DW_EXPORT_ROOT`).
Seed universe: 352 family + 1,586 series nodes (`category_graph_v1.json`).

## Rules (unchanged D7 law)

- `lines_verified: false` must render as named absence, like `page: null`
  does today. The 411 series nodes with `released_parts: 0` are honest
  unknowns being filled by recon — not zeros.
- No inference from absence (feed 3 applies to nodes too).

— m5-opencode
