# INSTITUTIONAL-CONTEXT-COMPILER — decision packets from real history

## What it is

Given a project event or proposed action, the compiler retrieves ONLY the
relevant institutional evidence into a reproducible, source-linked packet.
No conversation summaries, no unbounded context, no vector DB —
deterministic term retrieval over bounded sources (file indexes,
structured metadata, git, project-filtered stores).

## Evidence classes (every item classified)

| class | meaning | sources |
|---|---|---|
| authority | current approved policy/decision/PRD | DECISIONS.md entries, project config |
| verified-fact | measured/verified operational evidence | docs carrying measured evidence, incident verified_causes |
| assertion | agent-reported | agentmail, git commits |
| hypothesis | model-generated / suspected | incident suspected_causes, model_assessment |
| superseded | explicitly replaced — NEVER current authority | DECISIONS.md supersession links |
| unknown | contradictory/unclassifiable | named in `uncertainties` |

## Supersession law

`parse_decisions()` builds the D-graph from `supersedes` references; a
targeted entry becomes `superseded` and the packet's uncertainties note
that superseded material is excluded from current authority. Tests prove
a superseded PRD/decision is never presented as authority.

## Packet contract

`DecisionPacket{query, project_id, policy_version, items[{evidence_class,
text, source, freshness_age_d, authority}], uncertainties[]}`. Sources are
always `file:locator` or `git:sha`-style references. Freshness is age in
days (stale flags live in uncertainties).

## Isolation

Sources are rooted at the project's directory / project-filtered DB
queries; the cross-project test proves a Project-B query returns no
Project-A material. The cap protects authoritative/superseded items from
truncation — decision-makers always see the classes that bind them.
