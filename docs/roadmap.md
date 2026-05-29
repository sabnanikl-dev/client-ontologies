# Implementation Roadmap

This roadmap sequences the open enhancement issues plus the (not-yet-filed) runtime
consumer surface. It exists so the work lands in dependency order, one reviewable PR
at a time, instead of as a large undifferentiated batch.

It is documentation, not canonical ontology truth: it records *how* we plan to evolve
the repo, and is expected to change as issues are completed.

## Workflow: issues vs PRs

Per [`AGENTS.md`](../AGENTS.md) ("one issue, one branch, one PR"; builders never
self-approve/merge; docs update in the same PR):

- The existing enhancement issues (**#4–#12**) stay as issues — they become PRs one at
  a time, in the order below. Do **not** open many PRs at once.
- The **runtime consumer surface** is tracked in **#19** (Phase 1 below).
- Each issue → its own `scripts/issue-N` / `ontology/issue-N` / `schema/issue-N` branch
  → one focused PR → human review/merge → next.

## Why this order

Real dependencies drive the sequence:

- The runtime surface's copy check **is** issue #11's `machine_check` engine.
- Issue #4 (actions) and issue #10 (state-machine guards) both **reference** issue #9
  (approval gates), so #9 comes first.
- The runtime surface's provenance metadata overlaps issue #8.
- A shared loader refactor (`scripts/ontology_loader.py`) pays off in every later
  export/validator change, so it lands first.

## Phases

| Phase | Work | Depends on / why here |
|------|------|----------------------|
| **0 — Foundations** | Loader consolidation (`scripts/ontology_loader.py`, refactored out of `validate_ontology.py` / `export_sqlite.py`) + **#11** machine-check engine (`scripts/check_rules.py`) and `machine_check` payload validation | Smallest, self-contained primitives that the rest (incl. the runtime surface) build on |
| **1 — Runtime surface v1** | **#19:** shared stdlib core (`ontology_service.py`) + **CLI** (`ontology_cli.py`) for `list-clients` / `context` / `rules` / `check-copy` / `projection`; read-only; configurable YAML/SQLite source | Consumes #11's engine; delivers immediate ops value (e.g. a Femme-visibility pre-publish `check-copy` hook) |
| **2 — Governance primitives** | **#9** approval gates & records first-class → **#8** projection provenance/version metadata | Many resources point at approval gates; #8 lets the surface emit real provenance |
| **3 — Kinetic / workflow layer** | **#4** actions/functions → **#10** state machines → **MCP adapter** (+ read-only `list_actions` / `check_action_allowed`) | #4 and #10 reference #9; the MCP adapter extends the surface over the richer model |
| **4 — Modeling hygiene & consumer outputs** | **#5** interfaces/shared properties → **#12** client-safe handoff generation → **#6** lifecycle/deprecation/cleanup | #5 is the most invasive refactor; deferred to avoid premature abstraction with only 2 clients — **pull forward when a 3rd/4th client lands** |
| **5 — Speculative** | **#7** semantic search / Ontology-Augmented-Generation contract; future HTTP API adapter | Most forward-looking; the API adapter slots onto the existing core purely additively |

## Runtime consumer surface (Phase 1) shape

One shared, stdlib-first core with thin adapters, so the choice of surface is additive
rather than either/or:

```
scripts/ontology_loader.py   ── load + resolve projections (stdlib)
scripts/check_rules.py       ── machine_check engine (stdlib, = issue #11)
scripts/ontology_service.py  ── transport-agnostic ops, return plain JSON dicts
        │
        ├── scripts/ontology_cli.py        ← v1 NOW   (stdlib; CI / git-hook / test friendly)
        ├── server/ontology_mcp.py         ← NEXT     (thin MCP stdio adapter; mcp SDK, isolated)
        └── server/ontology_api.py         ← LATER    (thin HTTP adapter; purely additive)
```

v1 is **read-only** (no create/modify/delete) — modeling an operation must never grant
authority to run it (`AGENTS.md` core rule 6). The full v1 design lives in the runtime
surface issue.

## Gaps not yet tracked by any issue

Candidates for future issues, surfaced during the ontology review:

- Typed properties with per-property evidence/confidence (only partially covered by #5).
- Relationship cardinality, controlled predicate vocabulary, and inverse names
  (relationships are free-string today).
- Evidence verifiability: evidence line-ranges point at absolute local paths that are
  not in the repo, so CI and other agents cannot verify them — consider vendoring
  sanitized sources or using stable anchors.
- Wiring the unused `metric` entity type to targets/datasources.
