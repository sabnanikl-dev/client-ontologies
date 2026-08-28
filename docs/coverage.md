# Client Coverage Contract v0.1

Issue #41. Normative companion to `docs/spec.md` and the competency corpus
(`tests/competency/questions.yaml`, run by `tests/run_competency.py`).

A schema-valid ontology is **not** the same as a sufficient client ontology.
This contract states the durable question families a mature client ontology may
need to answer, records each current client's honest status per family, and
defines the three independent maturity dimensions that must not be conflated.

Coverage means **the ontology can answer a source-backed question** — it is not a
request to duplicate source-system rows, mass-expand taxonomy, or invent facts.
Where a client cannot answer a family honestly, it is marked a gap here rather
than papered over with an invented canonical fact (AGENTS.md core rule 3).

## Coverage status vocabulary

Each row-cell carries **exactly one** status token from this controlled
vocabulary. Where a family splits into a genuinely covered part and a gap/deferred
part, it is broken into separate sub-rows so no cell ever combines two statuses;
all nuance (planning-only, representation notes) lives in the basis column, never
in the status column.

| status | meaning |
| --- | --- |
| `covered` | the ontology contains evidence-backed resources that answer the family's question **and** a competency question retrieves that answer against the canonical export (cited in the basis column) |
| `known gap` | the concept is in scope for this client but not yet modeled with sufficient evidence; a real client fact is missing, not merely unqueried |
| `not applicable` | the family does not apply to this client's business (no gap to close) |
| `deferred / trigger-gated` | intentionally postponed until a concrete trigger (e.g. launch, a captured metric snapshot, a proven consumer need) — tracked, not forgotten |

`covered` is a *proof-carrying* status: it is used **only** when a competency
question in `tests/competency/questions.yaml` retrieves the answer, and the basis
column cites that question id. A resource that exists in the model but has no
competency proof is **not** marked `covered` — it is recorded as the honest status
it has earned (e.g. `known gap` when the client fact itself is missing). This keeps
the status column from conflating *model representation* with *client coverage*
(see the three maturity dimensions below).

## Coverage matrix

Families follow issue #41 §1. Citations are competency-question ids in
`tests/competency/questions.yaml`.

### Femme Events (`femme-events`)

| Question family | Status | Basis / proof |
| --- | --- | --- |
| Business identity & public offering | `covered` | the verified `brand.identity` and the approved/owner-reviewed `website.service-package` (offering) are retrieved by `business-identity-and-offering`; both are evidence-backed recorded resources |
| Audience / service area & operating constraints | `covered` | the owner-reviewed `visibility.service-area` (audience/area) is retrieved via the grounding chain in `gbp-grounded-in-owner-reviewed-fact`; the approval constraints are proven by `public-mutation-approval-rules` |
| People/roles & durable responsibility boundaries | `known gap` | `operations.approval-boundary.applies_to` names *what* is gated, but there is no named owner-roles entity (contrast JMD's `operations.owner-roles`); who approves is not yet a modeled, evidence-backed resource |
| Systems, repositories, domains, environments, systems of record | `covered` | `visibility.google-business-profile` (`system_resource`) and the local owner-reviewed system-of-record `visibility.business-fact` are retrieved in `gbp-grounded-in-owner-reviewed-fact`; the verified `website.site` (`system_resource`, the live web surface) is retrieved as an endpoint of the verified website flow in `website-content-data-flow`. Every cited system is thus retrieved by a named competency |
| Integrations & data flows between systems | `covered` | the verified website integration flow (Sanity CMS content → site, site → inquiry form) is proven by `website-content-data-flow` and the multi-hop content flow by `cms-content-renders-to-service-package-path`. The metric→GBP measurement edges are **planning-only** (`draft`, no evidence), proven to stay draft by the optional `outcome-metrics-measure-gbp-planning-only` — they are *not* counted as a source-backed integration answer |
| Workflows & approval boundaries | `covered` | approval workflow proven by `public-mutation-approval-rules`; `operations.photo-proof-inventory` is a `proposed` workflow artifact |
| Explicit workflow state machines | `known gap` | no explicit `state_machine` is modeled for Femme yet (contrast JMD's `inventory-images`) |
| Metric definitions (planning-only) | `deferred / trigger-gated` | three `draft` metric entities (`source_confidence: draft`, **no evidence**) are modeled as planning targets, not evidence-backed coverage. The required `local-visibility-outcome-metrics` is a **status-awareness safety check** proving they stay `draft` with `baseline: unknown` — it is *not* coverage proof (test metadata is not evidence). Promotion to an evidence-backed `covered` status is gated on a real read-only GBP snapshot |
| Observed metric values | `deferred / trigger-gated` | trigger-gated on a real read-only GBP snapshot; no achieved numbers are recorded or implied |
| Maintenance ownership, handoff responsibilities, lifecycle posture | `known gap` | lifecycle posture is partially carried by status vocabulary, but there is no handoff module (spec §4.1 lists `handoff/` as *Proposed*); handoff responsibilities are not yet modeled |

### JMD Menswear (`jmd-menswear`)

| Question family | Status | Basis / proof |
| --- | --- | --- |
| Business identity & differentiator | `covered` | the verified `brand.identity` and the durable one-of-a-kind `brand.differentiator` are retrieved by `brand-identity-and-differentiator`; both are evidence-backed, verified brand resources |
| Operating constraints | `covered` | the showroom-not-ecommerce and live-change constraints are proven by `ecommerce-and-live-change-guardrails`, and the approval boundary governing the public site by `approval-boundary-governs-site` |
| Public offerings (garment categories) | `deferred / trigger-gated` | `website.garment-category` is `proposed`/`source_confidence: draft` — a pre-launch, planning-only offering taxonomy, not an evidence-backed current offering. Gated on launch; no competency marks it `covered` |
| People/roles & durable responsibility boundaries | `covered` | the verified `operations.owner-roles` (Lucky leads relationships/brand; Danny leads inventory/purchasing/sales) is retrieved and proven verified by `owner-role-responsibility-split` |
| Systems, repositories, domains, environments, systems of record | `covered` | `website.site` is a verified `system_resource` (the current live surface), retrieved as the governed object in `approval-boundary-governs-site`. The Drive/Sanity systems (`inventory.drive-folder`, `inventory.sanity-asset`) are **proposed/`draft`** MVP pipeline targets, not a current system of record; they are traversed only as draft in `inventory-image-pipeline-path` |
| Integrations & data flows between systems | `covered` | the Drive→Sanity→showroom pipeline proven by the bounded multi-hop `inventory-image-pipeline-path`; every edge is `draft`, i.e. proposed MVP architecture (planning-only), not shipped — the guard requires every path edge to stay `draft` |
| Workflow resources & approval boundaries | `covered` | the curated inventory-workflow resources — including the human-approval rule `inventory.human-approval-required-for-mvp` and the operations approval boundary — are retrieved as declared projection resources by `inventory-workflow-resources`, and the verified boundary that governs the public site by `approval-boundary-governs-site` |
| Workflow actions & explicit state machine (states/transitions) | `deferred / trigger-gated` | `inventory-images` models an `image-lifecycle` `state_machine` (raw→review→approved→scheduled→published…), but it is `source_confidence: draft` — a planning-only MVP artifact — and **no** competency query retrieves state-machine internals (the bounded query vocabulary projects entities/rules/relationships/paths, not `state_machine` states/transitions; a state-machine query op is out of scope for #41). Representation exists; proof-carrying coverage is gated on launch, so this is not `covered` |
| Metric definitions, sources, cadence, planning-vs-observed status | `deferred / trigger-gated` | JMD is a pre-launch showroom with no outcome metrics yet; metric modeling is trigger-gated on launch. The schema *can* represent metrics (see maturity dimensions) — the client simply has none |
| Maintenance ownership, handoff responsibilities, lifecycle posture | `known gap` | many inventory resources are `proposed`/`draft` (lifecycle posture is legible), but no handoff module exists; handoff responsibilities are not yet modeled |

No client-specific follow-up issues are opened by this change: the gaps above are
recorded here as the evidenced backlog. Per issue #41, a client-specific
follow-up issue is created only after a gap is separately evidenced and approved
(AGENTS.md human gate) — not automatically from this matrix.

## The three independent maturity dimensions

These dimensions are orthogonal. A concept can satisfy one and fail the next;
conflating them is exactly how "schema-valid YAML" gets mistaken for "sufficient,
usable client coverage."

1. **Model representation** — *can the schema describe the concept at all?*
   Governed by `schemas/*.schema.json`: the entity-type vocabulary, the
   relationship predicate vocabulary, the rule model, evidence, projections.
   Example: the schema defines a `metric` entity type and a `measures` predicate,
   so *any* client **could** model outcome metrics.

2. **Client coverage** — *does this client's ontology actually contain
   evidence-backed resources that answer the question?* This is what the matrix
   above records and what `tests/run_competency.py` proves against the canonical
   export. Example: the schema represents a `metric` entity type for both clients,
   yet **neither** client has evidence-backed metric *coverage* — Femme carries
   three `draft` metric entities that are planning-only (`deferred / trigger-gated`,
   proven only to stay draft, **not** covered) and JMD has none at all.
   Representation is equal; coverage is absent for both. (Contrast a genuinely
   `covered` family such as Femme's business identity, where the retrieved
   `brand.identity` is verified and evidence-backed.) Representation ≠ coverage.

3. **Runtime queryability** — *can a supported consumer retrieve the answer
   safely, in scope, and status-aware?* The read-only runtime surface
   (`scripts/ontology_service.py` + `scripts/ontology_cli.py`, issue #19) serves
   entity, rule, and projection-resource views. The relationship and bounded-path
   competency queries added here are **test-owned**; their runtime consumer
   surface is **deferred** to the separate query-surface issue (issue #41 scope:
   "the read-only query-surface issue consumes this contract"). So a
   relationship/path answer is *covered and proven* at the competency layer while
   its *runtime queryability is deferred*.

### How the dimensions are tested

| Dimension | Enforced by |
| --- | --- |
| model representation | `scripts/validate_ontology.py` (schema layer) + `tests/run_fixtures.py` / `tests/run_predicates.py` |
| client coverage | `tests/run_competency.py` — each client answers its business/technical/multi-hop competency questions against the canonical export, with drift-, loading-, resolver-read-, relationship/path scope-isolation, path-shape (parallel-edge/cycle/branching/order **and expected-endpoint entity_type compatibility**), and reporting-seam (boolean row type-sensitivity **and** explicit `--no-drift` skipped-check representation) regressions, plus a registry shape-validation pass that rejects a malformed query/predicate/expect contract as a usage error before any answer is trusted. A `required` question is **gating**: it must pass and assert a **non-empty** expected answer (an empty required expectation with vacuous guards can never gate on anything). Being required does **not** by itself make a family `covered` — `covered` is the subset of required questions this matrix cites because the retrieved resources are evidence-backed; a required question may instead assert a status-awareness safety property over draft resources (gating, but not coverage). A deliberately empty answer must be `required: false` (a future explicit absence-query DSL stays out of scope). Filter/expected/field-guard operands are type-checked per column (a bool/number against a string column is rejected, and a `require_field_equals`/`require_field_in`/`forbid_field_in` operand on the boolean `public_facing` column must be a real `true`/`false`; `public_facing` booleans are normalized so a `false`/`0` type drift is caught in both row comparison and field-guard evaluation, not silently passed), and `severity` is validated against the schema enum. A failed **optional** question is non-gating and drift isolation is measured against the clean baseline, so an optional failure never turns the runner or a drift case red |
| runtime queryability | `tests/run_cli.py` — YAML/SQLite service parity for served ops; relationship/path ops are proven correct by the runner and explicitly deferred at the service layer |

## Competency query vocabulary (issue #41)

The corpus proves coverage with a small, deliberately bounded, deterministic
query vocabulary — **not** a general graph-query language, and with no GraphRAG,
embeddings, model grading, cross-client loading, or second graph store. See
`docs/examples.md` Example 7 for the full grammar and worked examples. In brief:

- `entities` / `rules` — projection-scoped row queries (filter + select).
- `projection_resources` — the projection's declared modules/entities/rules.
- `relationships` — subject/predicate/object edge rows; an edge surfaces **only**
  when its module is in scope **and both endpoints are in-scope entities**.
- `path` — bounded multi-hop traversal with explicit `start`/`end` constraints,
  an allowed-`predicates` list, and `min_hops`/`max_hops` bounds (simple paths,
  capped); every traversed node and edge stays inside the named projection.

Guards keep answers safe: status/field-membership guards prove a draft plan is
not presented as verified current architecture, and id-prefix / edge-confidence
guards prove no other client's or excluded module's resource leaks into an answer.
