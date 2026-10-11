# ASSURANCE — how to write a check that can actually fail

**Status:** ratified 2026-10-10 · **Scope:** every assertion, every reported number, every gate · **Companion:** `DECISIONS.md` D8 (fixtures are samples), `PRD-NORMALIZE` / `PRD-PHRASE` review checklists

---

## 0. Why this document exists

Between 2026-10-10 and now, the same class of defect cost **eight** rounds of
rework. Every one of them was a check that passed for a reason other than the
property it claimed to test.

| failure | what the assertion actually tested |
|---|---|
| D1's first gate: word bbox ≤ 1px, edge-for-edge | would have failed on **em-box vs ink-extent semantics**, not on bad geometry |
| `PRD-NORMALIZE` numbers: 43 symbols (41), 96 rating rows (63), 138 typed (141) | numbers **recalled from impression**, never counted |
| rating-class rules keyed on `table_kind` | tested against **my reading** of the gold; the gold showed qualifier-driven |
| `value_regex` missing `(?i)` | tests green because the curated fixture happened to use **uppercase units**; 81% of the real corpus uses lowercase |
| comparator sort by document position | I bent the **code** to satisfy a **stale fixture**, diverging from the live reference |
| OPNs summed across family documents → 131.2% coverage | `coverage >= floor` **passes at 131%** |
| `assert_eq!(with_page, 86736)` where `page` is NULL on every row | `Value::get` returns `Some(&Null)` for a present-but-null key — I tested **key presence** |
| `with_quote >= total * 9 / 10`, reported as 100% | passes at 90%; I cited the assertion, not the data |

**These are not eight mistakes. They are one mistake made eight times.**

---

## 1. The defect

> **A gate without a negative control is unfalsifiable by construction.**
>
> It will be green whether or not the property holds. And a green result that
> cannot go red is not evidence — it is decoration.

Three mechanisms, in order of how often each bit:

### 1.1 The assertion shares the code's blind spot

The test and the implementation are written from the same assumption, so the
test cannot see the thing that is wrong.

* `get("page").is_some()` in the *test* and `page: null` in the *data* — both
  treat "key exists" as "value exists".
* the comparator sort in the *code* and the expected order in the *fixture*
  — both encoded document position, and the live reference emitted table order.

**When the test and the code are wrong in the same way, green is meaningless.**

### 1.2 The bar is a floor, and floors do not discriminate

`x >= N` is satisfied by nonsense above `N`.

* `coverage >= 0.5` passed at **131.2%** because the denominator double-counted.
* `with_quote >= 90%` passed at 90% and I reported 100%.

A floor catches a collapse. It does not catch a lie. A gate that guards a
*percentage* needs a ceiling too, or needs the denominator counted.

### 1.3 Reported numbers come from assertions, not from queries

This is the one that does reputational damage. I wrote *"86,736/86,736 carry
quote + sha + page, so C3 and C13 are satisfied"* — into a commit message and
into a status report — from a test **I had just authored**. The truth was
`page: 0 / 86,736`.

**A test asserts. A report cites.** Those are different acts and the number
must come from different places.

---

## 2. The rules

### R1 — Name the failure condition before writing the assertion

Before `assert!(x)`, write down the input that makes it fail. If you cannot
name one, it is a comment, not a test.

> *"assert_eq!(with_page, 86736)"* — what makes this fail? A row with a null
> page. **But `get()` returns `Some` for a null**, so nothing makes it fail on
> the thing I cared about. The rule would have caught it immediately.

### R2 — Every gate has a negative control

For each gate, there is a paired test that **injects the fault and confirms
the gate goes red**. No negative control, no gate.

| gate | negative control |
|---|---|
| N1 / N4 condition parity | mutate one expected value → the gate must fail |
| N4b claim parity | flip one condition → the gate must fail |
| provenance floor | null out a page → the assertion must fire |
| coverage floor | set a knife to 0% → the floor must trip |
| `Stamp::from_coverage` | feed 0.49 → must be `Blocked` |
| `category_matches` | ask `Is` on an ancestor → must be `false`, not `true` |

A negative control is cheap. Its absence is not.

### R3 — Numbers in prose come from primary queries

Never cite a number produced by an assertion. Query the data directly, then
assert against the queried value.

```bash
# this is where a reported number comes from
python3 -c "import sqlite3; ... SELECT count(*) ..."
```

Then the assertion cites the *same* source, so the two can be compared.

### R4 — A percentage names a counted denominator

`131.2%` is only possible if the denominator was assumed. Every percentage
states its denominator and the denominator is **measured**:

* distinct set, not a sum — a set that double-counts looks like success
* a denominator can be zero; that is `Unknown`, not infinite coverage

### R5 — The live reference outranks any fixture

Already `DECISIONS.md` **D8**. Repeated because it was violated before it was
written:

> When a fixture and the live reference disagree, **run the reference**. Never
> bend the code toward a fixture. Never bend a fixture toward the code without
> recording it as a decision.

### R6 — Count before you claim

The three `PRD-NORMALIZE` numbers were recalled and wrong. Every cardinality
in a spec is produced by a query in the same session in which it is written,
and the query is recorded beside the number.

---

## 3. The mechanism: negative-control pairs

The single practice that would have caught all eight: **every gate ships with
its own falsifier.**

```rust
#[test]
fn gate_n4b_detects_divergence() {
    // Negative control: the gate must go red on a known-wrong expectation.
    // If this test passes while the gate is green on real data, the gate is
    // not discriminating.
    let row = CorpusRow { expected: json!({"reject": "dash"}), ..sample() };
    assert_ne!(parse_outcome(&row), row.expected, "the gate must be able to fail");
}
```

Where a negative control cannot be expressed as a unit test (a corpus gate, a
percentage floor), it is expressed as a **documented fault injection** run
before the gate is declared green, and the result is recorded.

---

## 4. What must change in this workspace

Audited against the rules above. Status is honest, not aspirational.

| gate | R2 negative control | R4 counted denominator | state |
|---|---|---|---|
| N1 condition 167/167 | **none** | exact count | add |
| N4a conditions 9551/9551 | **none** | exact count | add |
| N4b claims 10270/10270 | **none** | exact count | add |
| provenance `with_page == 0` | **none** | exact count | add |
| D4 coverage floor 632/625 | **none** | counted, weighted | add |
| `Stamp::from_coverage` | partial (`0.186` → `Blocked`) | n/a | **ok** |
| `category_matches` | partial (`Is` on ancestor) | n/a | **ok** |
| `Elimination::eliminates` | yes (explicit cases) | n/a | **ok** |
| G7 path segments | measured bar | n/a | **ok** |

**Nine gates. Three have a negative control.** That is the audit result and it
is the reason the same class recurred.

---

## 5. Application order

1. **Add negative-control pairs to the six gates that lack them.** Cheap, and
   they turn those gates from decoration into evidence.
2. **Re-report any number still standing on an assertion.** There is one
   outstanding: the C3/C13 claim, which is now corrected and asserted at 0
   pages.
3. **Every future spec check-in carries its count query** (R6) beside the
   number.

---

## 6. The standard

> A check is only evidence if it can be wrong.

Not "the tests are green." Not "the gate passed." The claim is narrower and
stronger: **this check would have failed if the property were false, and here
is the case that proves it.**

Until a gate has that, it does not belong in a status report.
