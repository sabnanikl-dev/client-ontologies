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

Each `(family, client)` cell carries exactly one status:

| status | meaning |
| --- | --- |
| `covered` | the ontology contains evidence-backed resources that answer the family's question; where marked, a competency question proves the answer against the canonical export |
| `known gap` | the concept is in scope for this client but not yet modeled with sufficient evidence; a real client fact is missing, not merely unqueried |
| `not applicable` | the family does not apply to this client's business (no gap to close) |
| `deferred / trigger-gated` | intentionally postponed until a concrete trigger (e.g. launch, a captured metric snapshot, a proven consumer need) — tracked, not forgotten |

A `covered` claim is only credible when a competency question can retrieve the
answer. Cited question ids below are the proof; a family marked `covered` with no
citation is representation-level only and is called out as such.

## Coverage matrix

Families follow issue #41 §1. Citations are competency-question ids in
`tests/competency/questions.yaml`.

### Femme Events (`femme-events`)

| Question family | Status | Basis / proof |
| --- | --- | --- |
| Business identity, offerings, audiences, operating constraints | `covered` | `brand.identity`, `website.service-package` (offering), `visibility.service-area` (audience/area); approval constraints proven by `public-mutation-approval-rules` and grounding proven by `gbp-grounded-in-owner-reviewed-fact` |
| People/roles & durable responsibility boundaries | `known gap` | `operations.approval-boundary.applies_to` names *what* is gated, but there is no named owner-roles entity (contrast JMD's `operations.owner-roles`); who approves is not yet a modeled, evidence-backed resource |
| Systems, repositories, domains, environments, systems of record | `covered` | `visibility.google-business-profile` and `website.site` are `system_resource`s; `visibility.business-fact` is the local system-of-record fact, proven in `gbp-grounded-in-owner-reviewed-fact` |
| Integrations & data flows between systems | `covered` | metric→system measurement flow proven by `outcome-metrics-measure-gbp-system`; content flow proven by the multi-hop `cms-content-renders-to-service-package-path` |
| Workflows, actions, state transitions, approval boundaries | `covered` (approval) / `known gap` (explicit state machines) | approval workflow proven by `public-mutation-approval-rules`; `operations.photo-proof-inventory` is a `proposed` workflow artifact; no explicit `state_machine` is modeled for Femme yet |
| Metric definitions, sources, cadence, planning-vs-observed status | `covered` (definitions, planning-only) / `deferred / trigger-gated` (observed values) | three `draft` metrics with `baseline: unknown`, proven planning-only by `local-visibility-outcome-metrics`; observed values are trigger-gated on a real read-only snapshot |
| Maintenance ownership, handoff responsibilities, lifecycle posture | `known gap` | lifecycle posture is partially carried by status vocabulary, but there is no handoff module (spec §4.1 lists `handoff/` as *Proposed*); handoff responsibilities are not yet modeled |

### JMD Menswear (`jmd-menswear`)

| Question family | Status | Basis / proof |
| --- | --- | --- |
| Business identity, offerings, audiences, operating constraints | `covered` | `brand.identity`/`brand.differentiator`, `website.garment-category` (offering); the showroom-not-ecommerce and live-change constraints proven by `ecommerce-and-live-change-guardrails` and `approval-boundary-governs-site` |
| People/roles & durable responsibility boundaries | `covered` (representation) / competency gap | `operations.owner-roles` (Lucky, Danny) is modeled as a `governance_object`; no competency question yet asserts the role split, so the proof is representation-level only |
| Systems, repositories, domains, environments, systems of record | `covered` | `inventory.drive-folder` and `website.site` are `system_resource`s; Sanity is the published system of record (`inventory.sanity-asset`), traversed in `inventory-image-pipeline-path` |
| Integrations & data flows between systems | `covered` (planning-only) | the Drive→Sanity→showroom pipeline proven by the bounded multi-hop `inventory-image-pipeline-path`; every edge is `draft`, i.e. proposed MVP architecture, not shipped |
| Workflows, actions, state transitions, approval boundaries | `covered` | `inventory-images` carries a `state_machine`; the human-approval boundary and workflow resources are proven by `inventory-workflow-resources` and the draft pipeline's approval-gate guard |
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
   export. Example: Femme **covers** metrics (three draft metric entities); JMD
   does **not** (a `deferred` gap) — even though the model represents metrics
   equally for both. Representation ≠ coverage.

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
| client coverage | `tests/run_competency.py` — each client answers its business/technical/multi-hop competency questions against the canonical export, with drift-, loading-, resolver-read-, and relationship/path scope-isolation regressions |
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
