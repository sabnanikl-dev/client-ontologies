# Client Operating Ontology Spec v0.1

> Status: Normative v0.1 contract — kept mechanically aligned with the live implementation.\
> Repository: `sabnanikl-dev/client-ontologies`  
> Scope: Agent-agnostic client ontology authoring, validation, projection, runtime consumption, and handoff.  
> Live YAML resource kinds: `client`, `ontology`, `ontology_module`, `projection`.\
> Design history and the original source inventory that informed this spec live in
> [`docs/research/initial-ontology-design.md`](research/initial-ontology-design.md);
> forward-looking sequencing lives in [`docs/roadmap.md`](roadmap.md).

**How to read this document.** It describes current, enforced repository behavior.
Sections that describe not-yet-built capabilities are marked **Proposed** or
**Trigger-gated**; absent such a marker, treat a statement as live contract. Inline
YAML blocks that use short `client.workstream` or `CamelCase` identifiers are
**illustrative shape sketches** — canonical live IDs follow
[`docs/conventions.md`](conventions.md) (lowercase, `client-slug.workstream.object-name`),
as shown in the live modules under `clients/` and in [`docs/examples.md`](examples.md).
The four live resource kinds are exactly `client`, `ontology` (the per-client
manifest), `ontology_module`, and `projection`.

---

## 1. Purpose

A **Client Operating Ontology** is a version-controlled, agent-agnostic semantic contract for doing client work.

It defines:

- the client workstreams currently in scope;
- the business and work-product entities involved in those workstreams;
- relationships between entities;
- approved facts and claims;
- content, safety, and approval rules;
- source systems and systems of record;
- repo/workflow projections;
- handoff exports that a client or future operator can understand.

It is not meant to be an academic ontology project first. It is meant to support real work such as:

- building and maintaining the Femme Events website;
- operating Femme Events local visibility/SEO artifacts;
- building and maintaining the JMD Menswear website/showroom;
- designing JMD Google Drive → CMS inventory-image workflows;
- creating handoff packages for clients or future operators;
- giving any capable agent, automation, or developer a shared source of truth that is not tied to one assistant's memory.

### 1.1 Primary goal

Make client work safer, more reusable, and more handoff-ready by moving client semantics out of chat memory and into reviewed files.

### 1.2 Non-goals for v0

The v0 ontology system does **not** aim to:

- replace GitHub issues, Linear, or project trackers;
- replace Sanity CMS or any client CMS;
- replace CRM/POS/accounting systems;
- become the only place where narrative client context lives;
- start with OWL/RDF/Neo4j as the required authoring layer;
- contain credentials, OAuth tokens, private raw exports, or secrets;
- encode instructions for only one agent or assistant implementation.

---

## 2. Design principles

### 2.1 Agent agnostic by default

The ontology must be usable by multiple consumers:

- coding agents;
- planning agents;
- review agents;
- deterministic scripts;
- CI checks;
- human developers;
- client-facing handoff docs;
- future hosted ontology UIs, dashboards, and client-work systems.

Therefore, canonical ontology files must avoid agent-specific language such as:

```yaml
# Avoid in canonical ontology
hermes_should: "..."
claude_should: "..."
codex_should: "..."
```

Use neutral consumer/action language instead:

```yaml
# Prefer
consumer_requirements:
  - id: public_content.requires_approval_check
    applies_to:
      - content_generation
      - website_publication
      - account_mutation
    requirement: "Before public use, verify an approved source or approval record exists."
```

Agent-specific operational behavior may live in skills, repo `AGENTS.md` files, or runtime adapters, but not in the canonical ontology schema.

### 2.2 Evidence over memory

Any client-specific fact that can affect public content, account changes, automation, or handoff must cite a source.

Valid evidence source types:

```yaml
evidence_source_types:
  - obsidian_note
  - local_project_doc
  - git_repo_file
  - github_issue
  - github_pr
  - linear_issue
  - client_email_or_message
  - public_url
  - api_readonly_snapshot
  - human_approval_record
```

Example:

```yaml
approved_claims:
  - id: femme.identity.primary_website
    value: "https://femmeevents.com"
    status: approved_internal
    evidence:
      - type: obsidian_note
        path: "/Users/creator/obsidian-vault/hermes-brain/wiki/femme-events/Femme Events Overview.md"
        lines: "17-18,37-41"
      - type: local_project_doc
        path: "/Users/creator/projects/femme-events/visibility/Femme-visibility/docs/femme-events/local-seo-source-of-truth.md"
        lines: "44-56,91-101"
```

An evidence reference may additionally carry two optional **portable anchors** so a
citation can be verified, not just pointed at: `snapshot_date` (the `YYYY-MM-DD` the
span was last confirmed) and `content_hash`, a versioned SHA-256 of the cited lines
in the exact form `sha256:utf8-lf-v1:<64 lowercase hex>`. `utf8-lf-v1` decodes the
source as UTF-8, normalizes CRLF/CR to LF, selects the 1-based inclusive `lines`
range(s), joins them with `\n` (no trailing newline), and SHA-256s the bytes — so a
line-ending-only change does not create false drift, and any future normalization
must use a new version tag. `scripts/check_evidence.py` re-hashes anchored spans and
reports drift; a repo-relative anchor is verified *portably* (any checkout/CI) while
an available external absolute path is verified *environment-locally* only (see §14.4
for that distinction and `docs/conventions.md` for the anchor-vs-vendor policy). Both
fields are optional and existing citations validate unchanged.

### 2.3 Authoring source and runtime stores are separate

Canonical authoring source in v0:

```text
YAML / JSON / Markdown files in git
```

Runtime consumers may compile projections into:

- SQLite for local lookup and validation;
- Postgres for hosted multi-user systems;
- Sanity for website content records;
- Google Sheets for lightweight ledgers;
- n8n workflows for deterministic reconciliation;
- RDF/OWL/Turtle later if formal graph reasoning becomes necessary.

Canonical ontology files remain the reviewed contract.

### 2.4 Workstream-first modeling

Client ontologies should be organized around work being done, not just abstract business categories.

Examples:

```yaml
workstreams:
  - id: website
  - id: local_visibility
  - id: cms_content
  - id: inventory_images
  - id: reporting
  - id: handoff
```

For JMD, `inventory_images` matters because a planned workflow involves Google Drive intake, approval, Sanity assets, website showroom cards, and archive states.

For Femme, `local_visibility` matters because there is a dedicated Femme Visibility repo and local SEO source-of-truth artifact.

### 2.5 Handoff-aware from the beginning

Every canonical module should be able to produce at least two views:

1. **Internal operating view** — for agents, developers, and automations.
2. **Client handoff view** — clean explanations of business objects, workflows, approval states, and maintenance responsibilities.

The client handoff view must remove:

- private file paths unless intentionally shared;
- internal-only notes;
- credentials and tokens;
- implementation-only agent routing details;
- unfinished assumptions not labeled as draft/unknown.

### 2.6 UI-first without SaaS lock-in

Future consumers may include a lightweight UI for browsing, editing, validating, and exporting client ontologies. That UI should be treated as an interface over this canonical repo, not as a requirement to turn the ontology system into a SaaS platform.

Design implications:

- schema IDs and ontology namespaces should stay neutral and portable;
- UI metadata should describe consumer views, forms, validation affordances, and safe actions without assuming a hosted product brand;
- canonical truth remains reviewed files in this repository unless a later architecture decision explicitly changes that;
- hosted dashboards, admin panels, or portals are consumers/projections, not the source of truth by default.

---

## 3. Source grounding

This spec was grounded in the client source material and repositories inventoried
during its original research pass. That inventory — verified GitHub repositories,
Obsidian/wiki notes, local project documents, and observed Linear planning issues — is
**historical context, not per-fact evidence**, and it has been relocated to
[`docs/research/initial-ontology-design.md`](research/initial-ontology-design.md) so
this document stays current. (The original inventory recorded this repository as empty;
it has since been implemented, which is exactly why that observation now lives in the
history file rather than here.)

Canonical ontology facts do not inherit that inventory as evidence: each active,
approved, or verified fact cites its own source in its module's `evidence_sources`
registry, resolved by the validator (see §9). The spec does not read or mutate Linear;
Linear references are planning history only.

---

## 4. Repository layout

Current repository layout (live):

```text
client-ontologies/
  README.md
  AGENTS.md                # process bible (roles, rules, gates, PR flow)
  CLAUDE.md                # mechanical orientation (file model, commands, gates)
  docs/
    spec.md                # this normative contract
    conventions.md
    examples.md
    roadmap.md             # live issue sequencing
    research/
      initial-ontology-design.md   # relocated design history + source inventory
  schemas/
    ontology.schema.json   # umbrella: oneOf-dispatches by kind
    defs.schema.json       # shared $defs: id/status/confidence
    evidence.schema.json   # shared $defs: sources + evidence refs
    rule.schema.json       # shared $defs: rule + machine_check
    client.schema.json
    manifest.schema.json   # the kind: ontology per-client manifest
    module.schema.json
    projection.schema.json
  scripts/
    ontology_loader.py     # shared YAML parse + manifest-first enumeration
    validate_ontology.py   # canonical gate: schema then cross-reference pass
    check_rules.py         # machine_check guardrail engine (library + CLI)
    check_evidence.py      # evidence-health / content_hash checker (library + CLI)
    export_sqlite.py       # runtime SQLite projection
  tests/
    run_fixtures.py        # invalid fixtures must fail validation
    run_export.py          # valid fixture must validate + export
    run_checks.py          # guardrail engine matching + exit semantics
    run_evidence.py        # evidence-health hashing + strict exit semantics
    fixtures/
  .github/
    workflows/
      validate.yml         # CI: validate, export, and run all three test runners
  clients/
    femme-events/
      client.yaml
      ontology.yaml        # manifest: entry point listing modules + projections
      modules/
        brand.yaml
        website.yaml
        local-visibility.yaml
        operations.yaml
      projections/
        agent-context.yaml
        website-build.yaml
        local-seo.yaml
    jmd-menswear/
      client.yaml
      ontology.yaml        # manifest: entry point listing modules + projections
      modules/
        brand.yaml
        website.yaml
        inventory-images.yaml
        operations.yaml
      projections/
        agent-context.yaml
        website-build.yaml
        inventory-workflow.yaml
```

> **Proposed / not yet built.** The original draft also sketched top-level `tools/`,
> `templates/`, `docs/decisions/`, and per-client `handoff/` directories. None exist
> today: tooling ships under `scripts/` (not `tools/`), and templates, ADR files, and
> client-safe handoff packages remain open, trigger-gated ideas (handoff generation is
> tracked in `docs/roadmap.md`). The superseded layout sketch is preserved in
> [`docs/research/initial-ontology-design.md`](research/initial-ontology-design.md).

### 4.1 What belongs where

#### `docs/`

Spec (this normative contract), conventions, consumption examples, the live roadmap,
and — under `docs/research/` — relocated design history. Architectural decision records
(`docs/decisions/`) are a **proposed** future addition, not a current directory.

#### `schemas/`

Machine-readable JSON Schemas for validating ontology files. These schemas are generic and client-independent.

The contract is split by resource `kind`: `client.schema.json`, `manifest.schema.json` (the `kind: ontology` per-client manifest), `module.schema.json`, and `projection.schema.json` dispatch off shared `$defs` in `defs.schema.json` (id/status/confidence), `evidence.schema.json` (sources and evidence references), and `rule.schema.json`. `ontology.schema.json` is the umbrella that `oneOf`-dispatches by kind. `scripts/validate_ontology.py` enforces the matching schema for each file (types, controlled-vocabulary enums, required identity fields, and `additionalProperties: false` — extensions must be `x_`-prefixed) *before* its repo-specific cross-reference and evidence checks. The validator ships a small dependency-free JSON Schema (draft 2020-12 subset) evaluator so the repo keeps no Python package requirements; negative fixtures in `tests/fixtures/` (run via `tests/run_fixtures.py`) prove malformed files are rejected.

#### `scripts/`

The live tooling: `ontology_loader.py` (shared manifest-first YAML enumeration used by
both the validator and exporter), `validate_ontology.py` (the canonical gate),
`check_rules.py` (the `machine_check` guardrail engine, importable and CLI),
`check_evidence.py` (the evidence-health / `content_hash` checker, importable and CLI),
and `export_sqlite.py` (the runtime SQLite projection). All are stdlib-only and shell out
to `ruby -e` for YAML parsing, so the repo carries no pip/gem dependencies.

#### `clients/<client-id>/`

Client-specific facts, rules, modules, and projections, entered through the client's
`ontology.yaml` manifest.

#### **Proposed:** `templates/` and `clients/<client-id>/handoff/`

Reusable domain/workstream templates and generated client-facing handoff documents were
part of the original design sketch. They are **not implemented yet**. Handoff packaging
(which must be safe to share only after human review) is tracked as an open issue in
`docs/roadmap.md`; template scaffolding relates to the open new-client scaffolding issue.

---

## 5. Canonical ontology file model

### 5.1 Client file

`clients/<client-id>/client.yaml`

Purpose: stable client metadata and high-level governance. It should not be overloaded with all modules.

```yaml
schema_version: "0.1"
kind: client
id: jmd-menswear
name: JMD Menswear
status: active
client_type: local_formalwear_retail

source_registry:
  - id: jmd-wiki-client-note
    type: obsidian_note
    path: "/Users/creator/obsidian-vault/hermes-brain/wiki/consultancy/clients/JMD/Client JMD Menswear.md"
    description: "JMD client profile, business model, constraints, project files, domain/DNS, GBP notes."

privacy:
  public_handoff_allowed: true
  contains_private_context: true
  secret_policy: "No credentials, OAuth tokens, raw private exports, or payment details."

workstreams:
  - id: website_showroom
    status: active
  - id: local_visibility
    status: active
  - id: inventory_images
    status: proposed
  - id: content_engine
    status: draft
  - id: reporting
    status: active
```

### 5.2 Ontology manifest file

`clients/<client-id>/ontology.yaml`

Purpose: the per-client manifest and **stable entry point**. It makes the client
ontology navigable, pins module/projection membership, and is the first file
agents and scripts should load before reading individual modules.

> Implementation note: the live repo uses `kind: ontology` with lowercase
> client-namespaced IDs. The earlier `kind: ontology_index` / `extends` /
> dotted-`jmd.*`-ID sketch below was design rationale and is **not** what the
> validator enforces — the shape shown here is canonical.

```yaml
schema_version: "0.1"
kind: ontology
id: jmd-menswear.ontology
client_id: jmd-menswear
status: active
modules:
  - path: modules/website.yaml
    id: jmd-menswear.website
  - path: modules/inventory-images.yaml
    id: jmd-menswear.inventory-images
projections:
  - path: projections/website-build.yaml
    id: jmd-menswear.website-build
  - path: projections/inventory-workflow.yaml
    id: jmd-menswear.inventory-workflow
handoff_outputs:        # optional: projections intended for client/agent handoff
  - id: jmd-menswear.agent-context
    target: generic_agent
notes: >                # optional free-text orientation
  Manifest and stable entry point for the JMD Menswear ontology.
```

The validator (`scripts/validate_ontology.py`) loads manifests first and checks
that every listed `path` exists, that each declared `id` matches the ID inside
the referenced file, and that no module/projection file is left unregistered.
`templates` is a reserved optional field for future shared-template references.

### 5.3 Module file

`clients/<client-id>/modules/<module>.yaml`

Purpose: workstream-specific entities, relationships, rules, systems, and evidence.

```yaml
schema_version: "0.1"
kind: ontology_module
id: jmd.inventory_images
title: "JMD Inventory Image Workflow"
client_id: jmd-menswear
status: draft
workstreams:
  - inventory_images
  - website_showroom

entities: []
relationships: []
rules: []
systems: []
state_machines: []
evidence: []
handoff: []
```

---

## 6. Standard entity model

Every entity should use a consistent shape.

```yaml
entities:
  - id: InventoryImage
    label: Inventory Image
    description: "A photo used to represent a showroom item or garment category."
    entity_type: work_product
    public_facing: true
    workstreams:
      - inventory_images
      - website_showroom
    lifecycle_states:
      - raw_uploaded
      - needs_review
      - approved
      - scheduled
      - published
      - archived
      - rejected
      - error
    source_systems:
      - google_drive
      - sanity
    related_entities:
      - InventoryItem
      - SanityAsset
      - ShowroomCard
      - ApprovalLedgerEntry
    approval_required: true
    handoff_label: "Website inventory photo"
    evidence:
      - source_id: jmd-inventory-plan
        lines: "184-237,238-310"
```

### 6.1 Entity type vocabulary

Recommended `entity_type` values:

```yaml
entity_types:
  - business_object       # Customer, Event, Garment, ServicePackage
  - work_product          # WebsitePage, CopyBlock, ShowroomCard
  - content_record        # SanityDocument, StaticFallbackData
  - media_asset           # ProductPhoto, BrandAsset, SanityAsset
  - workflow_artifact     # ApprovalLedgerEntry, SyncRun, HandoffDoc
  - system_resource       # DriveFolder, Repo, APIEndpoint
  - governance_object     # ApprovedClaim, ApprovalGate, Rule
  - metric                # CallRate, DirectionRequestRate, WebsiteClickRate
```

---

## 7. Relationship model

Relationships should be practical and implementation-useful.

### 7.1 Relationship object shape

**Implemented.** `subject`, `predicate`, `object`, and `source_confidence` are
required; `cardinality`, `inverse`, `description`, `workstreams`, and `evidence`
are optional. `predicate` and `inverse` are drawn from the controlled vocabulary
(§7.3); `cardinality` is one of `one_to_one`, `one_to_many`, `many_to_one`,
`many_to_many`, `unknown`.

```yaml
relationships:
  - id: jmd.image.creates_sanity_asset
    subject: InventoryImage
    predicate: creates_or_updates
    object: SanityAsset
    workstreams:
      - inventory_images
    cardinality: many_to_one
    inverse: sourced_from
    description: "An approved inventory image may create or update a Sanity asset for website delivery."
    evidence:
      - source_id: jmd-inventory-plan
        lines: "406-418"
```

### 7.2 Compact triple form (illustrative only — not accepted by the v0.1 schema)

> **Not implemented.** The compact `triples` form below is illustrative shorthand
> for discussing relationships in prose. It is **not** a canonical module shape:
> `schemas/module.schema.json` defines no top-level `triples` property and keeps
> `additionalProperties: false`, so a module using this form is rejected by
> `scripts/validate_ontology.py`. Author relationships with the implemented
> `relationships` object (§7.1). Adopting a compact form would require a
> deliberate schema PR and is out of scope for v0.1.

```yaml
# Illustrative shorthand only — NOT a valid module. Use the §7.1 `relationships` object.
triples:
  - [InventoryImage, creates_or_updates, SanityAsset]
  - [SanityAsset, renders_in, ShowroomCard]
  - [ShowroomCard, appears_on, WebsitePage]
```

### 7.3 Relationship predicates

**Implemented as a controlled vocabulary.** `predicate` is a schema enum
(`schemas/module.schema.json` `$defs.predicateName`), seeded from the generic set
below plus every predicate live in `clients/*/modules`. An unknown predicate
fails validation; adding one is a deliberate schema PR (see
`docs/conventions.md` for the full predicate table with meanings, inverses, and
domain/range).

```yaml
predicates:
  - appears_on
  - archived_by
  - contains
  - creates
  - creates_or_updates
  - governed_by
  - governs
  - measures
  - merges_with
  - must_match
  - overrides
  - renders_in
  - requires_approval_from
  - sourced_from
  - stored_in
  - supports
  - synchronized_by
  - targets
  - uses
```

**Experimental escape:** a predicate matching the bounded token pattern
`^x_[a-z][a-z0-9_]*(?![\s\S])` (e.g. `x_amplifies`) bypasses the enum for trialling,
mirroring the `x_` field-extension escape. The trailing `(?![\s\S])` is an
absolute-end negative lookahead — ECMAScript-portable syntax that pins the token to
the true end of string and behaves identically under the repo's Python evaluator and
any Draft 2020-12 (ECMAScript-regex) engine, without a Python-only anchor such as
`\Z`. The token must be complete and non-empty — a bare `x_`, embedded whitespace, or
a trailing newline is rejected, so the escape cannot reintroduce free-string drift.
An `x_` predicate carries no
domain/range constraints and is not a valid `inverse` value; promote it to a real
enum member once it stabilises.

**Bounded domain/range (semantic validation).** Beyond the enum, a small,
high-confidence subset in `scripts/validate_ontology.py` `PREDICATE_CONSTRAINTS`
pins the allowed subject/object `entity_type`. The cross-reference pass resolves
each endpoint and rejects a mismatch. Current constraints — each reflecting
semantics already true of every live relationship, deliberately *not* an
OWL-style class hierarchy or disjointness layer:

| Predicate | Constraint |
| --- | --- |
| `measures` | subject `entity_type` must be `metric` |
| `governed_by` | object `entity_type` must be `governance_object` |
| `contains` | subject `entity_type` must be `system_resource` |

Predicates outside this subset stay vocabulary-checked only. A deterministic
sync guard (`tests/run_predicates.py` plus a validator self-check) fails if any
constraint key or live `inverse` name leaves the schema enum.

---

## 8. Rule model

Rules are the enforcement and safety layer.

### 8.1 Rule shape

```yaml
rules:
  - id: jmd.website.no_ecommerce_language
    title: "Do not imply ecommerce behavior"
    status: active
    severity: blocking
    applies_to:
      entity_types:
        - WebsitePage
        - ShowroomCard
        - CopyBlock
      workstreams:
        - website_showroom
        - inventory_images
    rule_type: prohibited_claim
    statement: "Public JMD website content must not imply checkout, cart, live stock counts, or guaranteed inventory availability unless the operating model is explicitly approved later."
    machine_check:
      type: disallowed_terms
      disallowed_terms:
        - "add to cart"
        - "buy online"
        - "only 1 left"
        - "in stock"
        - "available now"
    evidence:
      - source_id: jmd-client-note
        lines: "51-54"
      - source_id: jmd-inventory-plan
        lines: "22-27,233-237"
```

### 8.2 Rule status vocabulary

```yaml
rule_status:
  draft: "Proposed by an operator/agent; not safe as public constraint yet."
  proposed: "Ready for human/domain review."
  active: "Approved or otherwise verified enough to govern work."
  deprecated: "No longer active; retained for history."
  prohibited: "Explicitly disallowed."
```

### 8.3 Rule severity vocabulary

```yaml
rule_severity:
  info: "Context only."
  warning: "Should be reviewed if violated."
  blocking: "Work should not proceed until resolved."
  approval_required: "Work may proceed as a draft but cannot be public/live without approval."
```

### 8.4 Machine-checkable rules

Rules should include machine checks when feasible.

The list below is the aspirational catalog. What the schema **enforces and the engine executes today** is a narrower v1 set — `schemas/rule.schema.json` type-discriminates `machine_check` with a `oneOf` that accepts exactly three types, and `scripts/check_rules.py` executes them:

- `disallowed_terms` — non-empty string array; violation on case-insensitive substring match.
- `required_terms` — non-empty string array; violation when any listed term is absent (case-insensitive).
- `regex_policy` — `pattern` (string) + `policy: allow | deny`; matched via `re.search` (`deny` = matching text violates, `allow` = absence violates). The schema only checks that `pattern` is a string, so `scripts/validate_ontology.py` additionally `re.compile`s every `regex_policy` pattern during its cross-reference pass — an uncompilable pattern is a validation failure, not a runtime crash in `scripts/check_rules.py`.

Unknown types and malformed payloads fail validation. The remaining catalog entries — notably `status_transition` and an `approval_required_pattern` gate check — are **reserved**: they depend on approval gates (#9) and state machines (#10) and are added as new `oneOf` branches only when those land, so v1 neither validates nor executes them.

```yaml
machine_check_types:
  - disallowed_terms
  - required_terms
  - required_fields
  - prohibited_fields
  - status_transition
  - url_canonicalization
  - schema_match
  - evidence_required
  - approval_required
```

Example:

```yaml
rules:
  - id: femme.cms.sparse_records_merge_with_fallback
    title: "Sparse CMS records must merge with fallback data"
    status: active
    severity: blocking
    applies_to:
      systems:
        - sanity
        - website_repo
      entities:
        - SanityDocument
        - StaticFallbackData
    rule_type: implementation_constraint
    statement: "If the CMS returns only a subset of records, website code must merge CMS records into static fallback data rather than replacing the whole fallback dataset."
    machine_check:
      type: required_behavior
      test_hint: "Find fetch/merge functions and verify fallback records remain visible when CMS returns one item."
    evidence:
      - source_id: femme-design-skill-derived-context
        note: "Existing skill context documents sparse CMS rule; future module must cite repo implementation or issue/PR once added."
```

---

## 9. Evidence model

Evidence prevents ontology drift and hallucinated public claims.

### 9.1 Evidence source registry

Each module should define source IDs once.

```yaml
evidence_sources:
  - id: jmd-client-note
    type: obsidian_note
    path: "/Users/creator/obsidian-vault/hermes-brain/wiki/consultancy/clients/JMD/Client JMD Menswear.md"
    description: "Client profile and constraints."

  - id: jmd-inventory-plan
    type: local_project_doc
    path: "/Users/creator/projects/consultancy/JMD-Menswear/deliverables/JMD-Website/docs/research/inventory-backend-automation-plan.md"
    description: "Draft JMD inventory backend and Drive automation plan."

  - id: linear-jmd-23
    type: linear_issue
    identifier: JMD-23
    url: "https://linear.app/papi-consultants/issue/JMD-23/build-deterministic-google-drive-to-sanity-photo-automation-for-jmd"
    description: "Observed planning issue for Drive to Sanity photo automation."
```

### 9.2 Evidence on claims

```yaml
claims:
  - id: femme.identity.canonical_phone
    value: "(678) 644-5257"
    status: owner_reviewed_internal
    public_safe: true
    evidence:
      - source_id: femme-local-seo-sot
        lines: "44-56"
```

### 9.3 Required evidence rules

Any of the following must have evidence before becoming `active`:

- public phone/email/address/URL;
- service/package name;
- pricing or starting price;
- location/service-area claim;
- owner/team role;
- customer-facing policy;
- public website copy rule;
- account mutation rule;
- workflow state that can publish/archive/delete;
- client handoff instruction.

---

## 10. Approval model

Approvals must be modeled separately from facts.

### 10.1 Approval gate shape

```yaml
approval_gates:
  - id: jmd.public_content.client_approval_required
    title: "JMD public content requires client approval"
    applies_to:
      - WebsitePage
      - ShowroomCard
      - GBPUpdate
      - SocialPost
    approver_roles:
      - client_owner
      - account_owner
    draft_allowed_without_approval: true
    public_or_live_allowed_without_approval: false
    evidence_required: true
```

### 10.2 Approval record shape

```yaml
approval_records:
  - id: approval.jmd.rentals.copy.2026-05-12
    source_type: client_email_or_message
    approved_by:
      - role: client_owner
        name: "Lucky/Danny"
    approved_at: "2026-05-12"
    scope:
      - "Tuxedo rental copy"
      - "Starting price wording"
      - "Wedding group timing wording"
    approved_values:
      tuxedo_rental_starting_price: "$209.99 and up"
      wedding_groups: "prefer appointments"
      wedding_group_timing: "minimum of 3–4 weeks"
    source_reference: "Future module must cite the original email/message or verified local ingestion doc."
```

### 10.3 Draft vs public mutations

Ontology consumers may use draft facts for planning if labeled correctly. They must not use draft facts for public/live changes.

```yaml
public_use_policy:
  draft: false
  proposed: false
  owner_reviewed_internal: true
  active: true
  approved: true
  unknown: false
  prohibited: false
```

---

## 11. Projection model

A projection is a repo-, workflow-, or handoff-specific slice of the canonical ontology.

### 11.1 Projection file shape

```yaml
schema_version: "0.1"
kind: projection
id: femme.website_repo_projection
client_id: femme-events
projection_target:
  type: github_repo
  repo: sabnanikl-dev/Femme-Events-Website
  local_paths:
    - "/Users/creator/projects/Femme-Events-Website"
    - "/Users/creator/projects/femme-events/website/Femme Events Website Build/Femme-Events-Website"

includes:
  modules:
    - femme.website
    - femme.cms
    - femme.brand_content
    - femme.approvals
  entities:
    - WebsitePage
    - ServicePackage
    - CopyBlock
    - SanityDocument
    - StaticFallbackData
    - CTA
  rules:
    - femme.copy.public_claims_require_approval
    - femme.cms.sparse_records_merge_with_fallback

outputs:
  - path: "docs/ontology/website-projection.yaml"
    format: yaml
  - path: "docs/ontology/content-rules.md"
    format: markdown
```

### 11.2 Projection destinations

Supported `projection_target.type` values:

```yaml
projection_target_types:
  - github_repo
  - local_repo
  - automation_workflow
  - cms_schema
  - handoff_package
  - sqlite_export
  - postgres_migration
  - skill_reference
```

### 11.3 Projection rules

- **Proposed (not yet enforced):** A projection should identify the source ontology
  commit or version it was built from. Live projections do not yet carry this
  provenance; adding projection provenance and runtime version metadata is tracked as an
  open issue in `docs/roadmap.md`.
- A projection must be smaller than the canonical ontology.
- A projection should include only the rules/entities needed by that repo/workflow/handoff.
- A projection should not include private internal notes unless the target is explicitly internal.
- Live projections are **hand-authored** curated slices validated against
  `projection.schema.json`; deterministic projection *generation* is a proposed future
  tool, not current behavior. Generated projections should be committed in a target repo
  only when they materially help future work.

---

## 12. Runtime storage guidance

### 12.1 Recommended v0 decision

Use version-controlled YAML/JSON/Markdown as the canonical source.

Do not start with a database as the authoring surface.

### 12.2 SQLite runtime export

Use SQLite for local agent/script lookup when:

- the ontology needs fast local queries;
- deterministic checks need to run without loading many files;
- a workflow needs a portable single-file artifact;
- no multi-user hosted app is needed.

`scripts/export_sqlite.py --output build/client-ontologies.sqlite` produces the live
single-file database. It creates nine tables — `manifests`, `clients`, `modules`,
`entities`, `relationships`, `rules`, `projections`, `sources`, and `evidence` — each row
carrying a `raw_json` column with the full canonical object. The schema below is
reproduced from the shipped `scripts/export_sqlite.py`; the three canonical-fact tables
and the supporting indexes are shown verbatim:

```sql
CREATE TABLE entities (
  client_id TEXT NOT NULL,
  module_id TEXT NOT NULL,
  entity_id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  status TEXT,
  source_confidence TEXT,
  public_facing INTEGER NOT NULL DEFAULT 0,
  raw_json TEXT NOT NULL
);

CREATE TABLE relationships (
  client_id TEXT NOT NULL,
  module_id TEXT NOT NULL,
  relationship_id TEXT PRIMARY KEY,
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object TEXT NOT NULL,
  source_confidence TEXT,
  raw_json TEXT NOT NULL
);

CREATE TABLE rules (
  client_id TEXT NOT NULL,
  module_id TEXT NOT NULL,
  rule_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  severity TEXT NOT NULL,
  rule_type TEXT NOT NULL,
  statement TEXT NOT NULL,
  source_confidence TEXT,
  raw_json TEXT NOT NULL
);

CREATE INDEX idx_modules_client ON modules(client_id);
CREATE INDEX idx_entities_client_type ON entities(client_id, entity_type);
CREATE INDEX idx_rules_client_status ON rules(client_id, status);
CREATE INDEX idx_rules_client_severity ON rules(client_id, severity);
CREATE INDEX idx_evidence_item ON evidence(item_id);
```

Example query against the live `rules` table:

```sql
SELECT rule_id, severity, statement
FROM rules
WHERE client_id = 'jmd-menswear'
  AND status IN ('active', 'approved')
  AND raw_json LIKE '%website_showroom%';
```

### 12.3 Postgres runtime storage — Proposed

Postgres is a **proposed** future runtime store, not part of the current
implementation. The only shipped runtime projection today is the SQLite export (§12.2).
Consider Postgres when:

- a hosted client portal needs ontology-backed behavior;
- multiple users need concurrent updates;
- approval records need robust audit trails;
- ontology projections must join with live operational data;
- workflow state transitions need durable locking/transactions.

Example minimal tables:

```sql
CREATE TABLE client_ontology_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id TEXT NOT NULL,
  ontology_version TEXT NOT NULL,
  git_commit_sha TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (client_id, ontology_version)
);

CREATE TABLE client_ontology_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ontology_version_id UUID NOT NULL REFERENCES client_ontology_versions(id),
  rule_id TEXT NOT NULL,
  status TEXT NOT NULL,
  severity TEXT NOT NULL,
  applies_to JSONB NOT NULL DEFAULT '{}'::jsonb,
  machine_check JSONB,
  raw JSONB NOT NULL,
  UNIQUE (ontology_version_id, rule_id)
);

CREATE TABLE client_approval_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id TEXT NOT NULL,
  approval_id TEXT NOT NULL,
  approved_by JSONB NOT NULL,
  approved_at TIMESTAMPTZ,
  scope JSONB NOT NULL DEFAULT '[]'::jsonb,
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  raw JSONB NOT NULL,
  UNIQUE (client_id, approval_id)
);
```

### 12.4 Sanity CMS relationship

Sanity should store website content records and media assets. It should not be the canonical ontology source in v0.

Good Sanity contents:

- journal posts;
- testimonials;
- vendor listings;
- JMD showroom items;
- image assets;
- editable copy blocks if deliberately modeled.

Ontology responsibilities:

- define what a `ShowroomItem` means;
- define which public fields are allowed;
- define approval states;
- define prohibited e-commerce claims;
- define sparse CMS fallback behavior;
- inform Sanity schema design.

Example Sanity schema snippet informed by ontology:

```ts
import {defineField, defineType} from 'sanity'

export const showroomItem = defineType({
  name: 'showroomItem',
  title: 'Showroom Item',
  type: 'document',
  fields: [
    defineField({name: 'title', type: 'string', validation: Rule => Rule.required()}),
    defineField({name: 'slug', type: 'slug', options: {source: 'title'}}),
    defineField({name: 'mainImage', type: 'image', options: {hotspot: true}, validation: Rule => Rule.required()}),
    defineField({name: 'altText', type: 'string', validation: Rule => Rule.required()}),
    defineField({
      name: 'category',
      type: 'string',
      options: {list: ['suit', 'tuxedo', 'jacket', 'shirt', 'shoes', 'accessories', 'rental_look', 'other']},
    }),
    defineField({name: 'sourceDriveFileId', type: 'string', readOnly: true}),
    defineField({name: 'approvedForWebsite', type: 'boolean', initialValue: false}),
    defineField({
      name: 'status',
      type: 'string',
      options: {list: ['intake', 'ready', 'scheduled', 'published', 'archived', 'sold', 'rejected']},
      initialValue: 'intake',
    }),
    defineField({name: 'publishedAt', type: 'datetime'}),
    defineField({name: 'archiveAt', type: 'datetime'}),
    defineField({name: 'internalNotes', type: 'text'}),
  ],
})
```

### 12.5 RDF/OWL/graph export — Proposed

RDF/OWL is a **proposed** optional later export, not a v0 dependency and not
implemented today.

Use it when:

- cross-client relationship queries become important;
- formal inference is needed;
- external semantic-web interoperability is valuable;
- the ontology grows beyond YAML lookup patterns.

Example Turtle-style export from a simple relationship:

```ttl
@prefix co: <urn:client-ontologies:ontology:client#> .
@prefix jmd: <urn:client-ontologies:ontology:clients:jmd-menswear#> .

jmd:InventoryImage a co:WorkProductEntity ;
  co:createsOrUpdates jmd:SanityAsset ;
  co:governedBy jmd:NoRawDrivePublishRule .

jmd:NoRawDrivePublishRule a co:BlockingRule ;
  co:statement "Raw Drive uploads must not publish directly to the public website." .
```

---

## 13. Validation and tooling

### 13.1 Minimum validator responsibilities

> **Historical design intent, not the current enforced contract.** This list is the
> original sketch of validator responsibilities; the authoritative behavior is whatever
> `scripts/validate_ontology.py` actually enforces (schema shape via `schemas/`, ID
> hygiene/uniqueness, evidence conditions, reference resolution, `regex_policy`
> compilation, manifest membership, and secret/sensitive-field scanning). Items below
> that the shipped validator does not implement are flagged **Proposed**.

A validator should check:

- YAML/JSON parse success;
- required fields exist;
- IDs are unique within module;
- referenced entities exist;
- referenced source IDs exist;
- active/approved claims have evidence;
- public-facing rules have either evidence or explicit draft status;
- projections reference existing modules/rules/entities;
- **Proposed (not implemented):** handoff exports do not include obvious private paths or
  secrets unless explicitly marked internal — the shipped validator scans canonical YAML
  for secret tokens and a fixed set of sensitive field names, but has no handoff-export
  validation path (no handoff export is built yet — the `clients/<client-id>/handoff/`
  layout is itself marked **Proposed** above).

> **Superseded by the shipped implementation.** The validator and JSON Schema code
> blocks in §13.2 and §13.3 are the original design *sketches*. They are retained to
> explain intent, but the live, authoritative implementation is
> `scripts/validate_ontology.py` (built on the shared `scripts/ontology_loader.py`) and
> the eight files under `schemas/`. Where a sketch and the shipped code differ, the
> shipped code wins; do not treat these snippets as the current contract.

### 13.2 Python validator sketch (historical)

```python
#!/usr/bin/env python3
"""Validate client ontology modules.

This is a sketch for docs/spec.md. The shipped implementation lives in
`scripts/validate_ontology.py` (not the originally proposed `tools/` path) and loads
JSON Schemas from `schemas/`.
"""
from __future__ import annotations

import sys
from pathlib import Path
import yaml

REQUIRED_MODULE_FIELDS = {"schema_version", "kind", "id", "client_id", "status"}
PUBLIC_STATUSES = {"active", "approved", "owner_reviewed_internal"}
SECRET_PATTERNS = ["token", "secret", "api_key", "oauth", "password"]


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a mapping")
    return data


def validate_module(path: Path) -> list[str]:
    errors: list[str] = []
    data = load_yaml(path)

    missing = REQUIRED_MODULE_FIELDS - set(data)
    if missing:
        errors.append(f"{path}: missing required fields: {sorted(missing)}")

    source_ids = {src.get("id") for src in data.get("evidence_sources", []) if isinstance(src, dict)}
    entity_ids = {ent.get("id") for ent in data.get("entities", []) if isinstance(ent, dict)}

    if len(entity_ids) != len([ent for ent in data.get("entities", []) if isinstance(ent, dict)]):
        errors.append(f"{path}: duplicate or missing entity IDs")

    for rel in data.get("relationships", []):
        if not isinstance(rel, dict):
            continue
        for field in ("subject", "object"):
            value = rel.get(field)
            if value and value not in entity_ids:
                errors.append(f"{path}: relationship {rel.get('id')} references unknown {field}: {value}")

    for claim in data.get("claims", []):
        if not isinstance(claim, dict):
            continue
        status = claim.get("status")
        if status in PUBLIC_STATUSES and not claim.get("evidence"):
            errors.append(f"{path}: public-safe claim {claim.get('id')} lacks evidence")

    text = path.read_text(encoding="utf-8").lower()
    for pattern in SECRET_PATTERNS:
        if pattern in text:
            # This is intentionally conservative; allowlist later with explicit fields.
            errors.append(f"{path}: possible secret-bearing term found: {pattern}")

    for src_ref in _iter_source_refs(data):
        if source_ids and src_ref not in source_ids:
            errors.append(f"{path}: unknown evidence source_id: {src_ref}")

    return errors


def _iter_source_refs(value):
    if isinstance(value, dict):
        if "source_id" in value:
            yield value["source_id"]
        for child in value.values():
            yield from _iter_source_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_source_refs(child)


def main(root: str) -> int:
    root_path = Path(root)
    errors: list[str] = []
    for path in root_path.glob("clients/*/modules/*.yaml"):
        errors.extend(validate_module(path))

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print("ontology validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
```

### 13.3 JSON Schema sketch (historical)

The single-file schema below predates the delivered contract, which is **split by
`kind`** across `schemas/` (see §4.1) rather than kept as one module schema. It is kept
only to show the original shape.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://client-ontologies.local/schemas/client-ontology-module.schema.json",
  "title": "Client Ontology Module",
  "type": "object",
  "required": ["schema_version", "kind", "id", "client_id", "status"],
  "properties": {
    "schema_version": {"type": "string"},
    "kind": {"const": "ontology_module"},
    "id": {"type": "string", "pattern": "^[a-z0-9_.-]+$"},
    "title": {"type": "string"},
    "client_id": {"type": "string", "pattern": "^[a-z0-9-]+$"},
    "status": {"enum": ["draft", "proposed", "active", "deprecated"]},
    "workstreams": {"type": "array", "items": {"type": "string"}},
    "entities": {
      "type": "array",
      "items": {"$ref": "#/$defs/entity"}
    },
    "relationships": {
      "type": "array",
      "items": {"$ref": "#/$defs/relationship"}
    },
    "rules": {
      "type": "array",
      "items": {"$ref": "#/$defs/rule"}
    }
  },
  "$defs": {
    "entity": {
      "type": "object",
      "required": ["id", "label", "description", "entity_type"],
      "properties": {
        "id": {"type": "string", "pattern": "^[A-Z][A-Za-z0-9]+$"},
        "label": {"type": "string"},
        "description": {"type": "string"},
        "entity_type": {"type": "string"},
        "public_facing": {"type": "boolean"},
        "workstreams": {"type": "array", "items": {"type": "string"}}
      }
    },
    "relationship": {
      "type": "object",
      "required": ["id", "subject", "predicate", "object"],
      "properties": {
        "id": {"type": "string"},
        "subject": {"type": "string"},
        "predicate": {"type": "string"},
        "object": {"type": "string"},
        "cardinality": {"enum": ["one_to_one", "one_to_many", "many_to_one", "many_to_many", "unknown"]}
      }
    },
    "rule": {
      "type": "object",
      "required": ["id", "title", "status", "severity", "statement"],
      "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "status": {"enum": ["draft", "proposed", "active", "deprecated", "prohibited"]},
        "severity": {"enum": ["info", "warning", "blocking", "approval_required"]},
        "statement": {"type": "string"},
        "machine_check": {"type": "object"}
      }
    }
  }
}
```

---

## 14. Consumer patterns

### 14.1 Generic TypeScript ontology loader

```ts
import fs from 'node:fs/promises'
import path from 'node:path'
import yaml from 'yaml'

export type OntologyRule = {
  id: string
  title: string
  status: 'draft' | 'proposed' | 'active' | 'deprecated' | 'prohibited'
  severity: 'info' | 'warning' | 'blocking' | 'approval_required'
  statement: string
  machine_check?: Record<string, unknown>
}

export type OntologyModule = {
  schema_version: string
  kind: 'ontology_module'
  id: string
  client_id: string
  status: string
  rules?: OntologyRule[]
}

export async function loadOntologyModule(filePath: string): Promise<OntologyModule> {
  const raw = await fs.readFile(filePath, 'utf8')
  const data = yaml.parse(raw) as OntologyModule
  if (data.kind !== 'ontology_module') {
    throw new Error(`${filePath} is not an ontology module`)
  }
  return data
}

export function activeBlockingRules(module: OntologyModule): OntologyRule[] {
  return (module.rules ?? []).filter(
    rule => ['active', 'approved', 'prohibited'].includes(rule.status) && rule.severity === 'blocking',
  )
}

export async function loadProjectionRules(root: string, projection: {modules: string[]}): Promise<OntologyRule[]> {
  const rules: OntologyRule[] = []
  for (const modulePath of projection.modules) {
    const mod = await loadOntologyModule(path.join(root, modulePath))
    rules.push(...activeBlockingRules(mod))
  }
  return rules
}
```

### 14.2 Generic copy/content check

This is the reference sketch. The shipped, importable implementation is
`scripts/check_rules.py` (stdlib-only, CLI + library) — consumers should call it
rather than re-derive the matching logic (see docs/examples.md, Example 6).

```python
from dataclasses import dataclass

@dataclass
class Violation:
    rule_id: str
    message: str
    severity: str


def check_disallowed_terms(text: str, rules: list[dict]) -> list[Violation]:
    lower = text.lower()
    violations: list[Violation] = []
    for rule in rules:
        machine_check = rule.get("machine_check") or {}
        if machine_check.get("type") != "disallowed_terms":
            continue
        for term in machine_check.get("disallowed_terms", []):
            if term.lower() in lower:
                violations.append(
                    Violation(
                        rule_id=rule["id"],
                        message=f"Disallowed term found: {term}",
                        severity=rule.get("severity", "warning"),
                    )
                )
    return violations
```

### 14.3 Generic status transition check

```python
ALLOWED_TRANSITIONS = {
    "raw_uploaded": {"needs_review", "rejected"},
    "needs_review": {"approved", "rejected"},
    "approved": {"scheduled", "archived"},
    "scheduled": {"published", "archived"},
    "published": {"archived", "sold"},
    "archived": {"approved"},
    "rejected": set(),
    "error": {"needs_review"},
}


def assert_transition_allowed(old: str, new: str) -> None:
    if new not in ALLOWED_TRANSITIONS.get(old, set()):
        raise ValueError(f"Illegal status transition: {old} -> {new}")
```

### 14.4 Generic evidence health check

`scripts/check_evidence.py` (stdlib-only, CLI + library) verifies the portable
citation anchors from §2.2. It reports at **two independent levels**, never
conflated or double-counted:

- **Sources** — one row per registry source that declares a `path`, *whether or not
  any citation references it*, so an uncited-but-declared source is still visible.
  Categories: `present`, `missing` (a repo-relative path that does not exist —
  strict-gating), and `unavailable_in_environment` (an external absolute path
  unavailable here — advisory). Existence, not file-ness: a directory used as a
  provenance pointer counts as `present`.
- **Citations** — one row per `evidence` ref. Where a `content_hash` anchor is
  present the cited span is re-hashed under `utf8-lf-v1`; categories: `verified_match`,
  `content_drift`, `source_missing`, `anchor_missing`, `invalid_range`,
  `unsupported_hash_version`, or `unresolvable_in_environment`.

Verification is **portable vs environment-local**, and the tool never overclaims:
only repo-relative sources (relative paths, or absolute paths that resolve inside the
repo root) are verified *portably* (any checkout/CI) — reported with
`scope: portable`. An available external absolute path is verified
*environment-locally* only: it is hashed and reported `verified_match` /
`content_drift` with `scope: environment_local` — a real check on this machine, but
never presented as a portable/CI guarantee. An external absolute path unavailable
here is `unresolvable_in_environment` (citation) / `unavailable_in_environment`
(source) and stays **advisory** — never collapsed into a false `verified_match`.

Exit behavior: without `--strict` the command is a pure report (exit 0); with
`--strict` it exits 1 only on a genuine failure — citation `content_drift`,
`source_missing`, `invalid_range`, `unsupported_hash_version`, or source `missing` —
and never on the advisory categories, so an unknown `--client` is a usage error
(exit 2). This is why the CI step can run `--strict` and still be non-blocking for
owner-only source paths. `docs/conventions.md` documents the anchor-vs-vendor
decision and the re-confirmation workflow.

---

## 15. Client-specific module seeds based on verified context

This section gives starter module seeds. They are not complete canonical ontology modules yet. Before public/live use, each client file must include evidence on each fact/rule.

### 15.1 Femme Events verified context summary

Verified from inspected sources:

- Femme Events is a wedding coordination + partial planning / design brand.
- Co-founders in the wiki source: Amanda Brewton and Karan Sabnani.
- Market: Metro Atlanta wedding coordination/design.
- Website: `https://femmeevents.com`.
- Current repo path in wiki: `/Users/creator/projects/femme-events/website/Femme Events Website Build/Femme-Events-Website`.
- GitHub website repo verified: `sabnanikl-dev/Femme-Events-Website`.
- Visibility ops repo verified: `sabnanikl-dev/Femme-visibility`.
- Femme website README verified stack: React + TypeScript, Vite, Tailwind CSS, React Router, Sanity CMS, Formspree.
- Local SEO source of truth identifies canonical phone `(678) 644-5257`, public email `Amanda@FemmeEvents.com`, public address posture as service-area/no storefront, and live package names `In Your Corner`, `Getting It Together`, `The Full Femme`.
- Public/account mutations remain approval-gated in the visibility source of truth and AGENTS.md.

Starter module outline:

```yaml
schema_version: "0.1"
kind: ontology_module
id: femme.website
client_id: femme-events
status: draft
title: "Femme Events Website Ontology"
workstreams:
  - website
  - cms_content
  - inquiry_ops

evidence_sources:
  - id: femme-overview
    type: obsidian_note
    path: "/Users/creator/obsidian-vault/hermes-brain/wiki/femme-events/Femme Events Overview.md"
  - id: femme-website-readme
    type: git_repo_file
    path: "/Users/creator/projects/Femme-Events-Website/README.md"
  - id: femme-local-seo-sot
    type: local_project_doc
    path: "/Users/creator/projects/femme-events/visibility/Femme-visibility/docs/femme-events/local-seo-source-of-truth.md"

entities:
  - id: WebsitePage
    label: Website Page
    entity_type: work_product
    description: "A public page or route on the Femme Events website."
    public_facing: true
  - id: ServicePackage
    label: Service Package
    entity_type: business_object
    description: "A public Femme Events service/package offering."
    public_facing: true
  - id: SanityDocument
    label: Sanity Document
    entity_type: content_record
    description: "CMS-backed website content served from Sanity."
    public_facing: true
  - id: StaticFallbackData
    label: Static Fallback Data
    entity_type: content_record
    description: "Repo-stored fallback data used when Sanity is unavailable or incomplete."
    public_facing: true
  - id: InquiryForm
    label: Inquiry Form
    entity_type: work_product
    description: "Website form that routes inquiries through Formspree."
    public_facing: true

relationships:
  - id: femme.page.contains_service_package
    subject: WebsitePage
    predicate: contains
    object: ServicePackage
  - id: femme.sanity.merges_with_fallback
    subject: SanityDocument
    predicate: merges_with
    object: StaticFallbackData
  - id: femme.cta.targets_inquiry_form
    subject: WebsitePage
    predicate: targets
    object: InquiryForm

rules:
  - id: femme.public_mutations.require_approval
    title: "Public/account mutations require explicit approval"
    status: active
    severity: approval_required
    statement: "Website, GBP, directory, review, social, and public-account mutations require explicit approval before execution."
    evidence:
      - source_id: femme-local-seo-sot
        lines: "32-40"
```

### 15.2 Femme visibility module seed

```yaml
schema_version: "0.1"
kind: ontology_module
id: femme.local_visibility
client_id: femme-events
status: draft
title: "Femme Events Local Visibility Ontology"
workstreams:
  - local_visibility
  - reporting

evidence_sources:
  - id: femme-local-seo-sot
    type: local_project_doc
    path: "/Users/creator/projects/femme-events/visibility/Femme-visibility/docs/femme-events/local-seo-source-of-truth.md"
  - id: linear-papi-44
    type: linear_issue
    identifier: PAPI-44
    url: "https://linear.app/papi-consultants/issue/PAPI-44/task-03-create-local-seo-source-of-truth-and-claims-guardrails"
  - id: linear-papi-56
    type: linear_issue
    identifier: PAPI-56
    url: "https://linear.app/papi-consultants/issue/PAPI-56/review-and-approve-femme-local-seo-source-of-truth"

entities:
  - id: GoogleBusinessProfile
    label: Google Business Profile
    entity_type: system_resource
    public_facing: true
    description: "Femme Events public Google Business Profile."
  - id: CitationListing
    label: Citation Listing
    entity_type: work_product
    public_facing: true
    description: "A directory or citation profile for local visibility."
  - id: BusinessFact
    label: Business Fact
    entity_type: governance_object
    public_facing: true
    description: "A canonical public fact such as phone, email, website, category, service area, or address posture."
  - id: ReviewRequest
    label: Review Request
    entity_type: work_product
    public_facing: true
    description: "A message/template/request asking for a customer review."

rules:
  - id: femme.visibility.no_public_address_without_approval
    title: "Do not publish a street address without approval"
    status: active
    severity: blocking
    statement: "Femme Events should remain service-area/no public storefront unless Amanda/Karan explicitly approve a public address later."
    evidence:
      - source_id: femme-local-seo-sot
        lines: "59-89"
```

### 15.3 JMD Menswear verified context summary

Verified from inspected sources:

- JMD Menswear is a specialty men's formal wear store in Conyers, GA.
- Owners in the wiki source: Lucky and Danny.
- Business model includes sales and rentals.
- Differentiator in the wiki source: one size per style / one-of-a-kind pieces / when it is gone, it is gone.
- Key constraints in the wiki source: Lucky does not want AI slop; Danny is cautious about e-commerce; e-commerce deferred.
- JMD project files live under `/Users/creator/projects/consultancy/JMD-Menswear/`.
- GitHub website/harness repo verified: `sabnanikl-dev/jmd-6-holding-page-harness`.
- Draft inventory automation plan recommends Google Drive intake, Sanity website backend/content model, an operational ledger, and a scheduled automation runner. The plan is explicitly draft for Karan review and no live account changes authorized.
- JMD inventory plan states the website needs a showroom feed, not e-commerce, and public language should avoid guaranteed availability, quantities, checkout, or warehouse vibes.
- Linear issue `JMD-23` exists for deterministic Google Drive → Sanity photo automation; `JMD-30` exists for creating a JMD client operating ontology v0.

Starter module outline:

```yaml
schema_version: "0.1"
kind: ontology_module
id: jmd.website_showroom
client_id: jmd-menswear
status: draft
title: "JMD Website Showroom Ontology"
workstreams:
  - website_showroom
  - local_visibility

evidence_sources:
  - id: jmd-client-note
    type: obsidian_note
    path: "/Users/creator/obsidian-vault/hermes-brain/wiki/consultancy/clients/JMD/Client JMD Menswear.md"
  - id: jmd-inventory-plan
    type: local_project_doc
    path: "/Users/creator/projects/consultancy/JMD-Menswear/deliverables/JMD-Website/docs/research/inventory-backend-automation-plan.md"

entities:
  - id: WebsitePage
    label: Website Page
    entity_type: work_product
    public_facing: true
    description: "A public page or route on the JMD website."
  - id: ShowroomCard
    label: Showroom Card
    entity_type: work_product
    public_facing: true
    description: "A website card representing a visual showroom highlight, not a purchasable SKU."
  - id: GarmentCategory
    label: Garment Category
    entity_type: business_object
    public_facing: true
    description: "A category of formalwear or accessory offered/showcased by JMD."
  - id: ApprovedClaim
    label: Approved Claim
    entity_type: governance_object
    public_facing: true
    description: "A public-safe claim with evidence/approval."

rules:
  - id: jmd.website.showroom_not_ecommerce
    title: "Website is showroom, not ecommerce"
    status: active
    severity: blocking
    statement: "JMD website content must frame inventory as showroom highlights, not live e-commerce inventory."
    evidence:
      - source_id: jmd-client-note
        lines: "51-54"
      - source_id: jmd-inventory-plan
        lines: "20-27,233-237"
```

### 15.4 JMD inventory image module seed

```yaml
schema_version: "0.1"
kind: ontology_module
id: jmd.inventory_images
client_id: jmd-menswear
status: draft
title: "JMD Inventory Images and Showroom Sync Ontology"
workstreams:
  - inventory_images
  - website_showroom

evidence_sources:
  - id: jmd-inventory-plan
    type: local_project_doc
    path: "/Users/creator/projects/consultancy/JMD-Menswear/deliverables/JMD-Website/docs/research/inventory-backend-automation-plan.md"
  - id: linear-jmd-23
    type: linear_issue
    identifier: JMD-23
    url: "https://linear.app/papi-consultants/issue/JMD-23/build-deterministic-google-drive-to-sanity-photo-automation-for-jmd"

entities:
  - id: DriveFolder
    label: Drive Folder
    entity_type: system_resource
    description: "Google Drive folder used for JMD image intake, approval, import, publication, or archive organization."
  - id: InventoryImage
    label: Inventory Image
    entity_type: media_asset
    public_facing: true
    description: "Image uploaded for potential use in the JMD showroom workflow."
  - id: ApprovalLedgerEntry
    label: Approval Ledger Entry
    entity_type: workflow_artifact
    description: "Record of Drive file status, approval status, import status, publish/archive status, notes, and errors."
  - id: SanityAsset
    label: Sanity Asset
    entity_type: media_asset
    public_facing: true
    description: "Image asset uploaded to Sanity for website delivery."
  - id: ShowroomItem
    label: Showroom Item
    entity_type: content_record
    public_facing: true
    description: "Sanity document representing a visual showroom highlight."
  - id: SyncRun
    label: Sync Run
    entity_type: workflow_artifact
    description: "A deterministic reconciliation execution and its summary."

relationships:
  - id: jmd.drive_folder.contains_inventory_image
    subject: DriveFolder
    predicate: contains
    object: InventoryImage
  - id: jmd.inventory_image.creates_sanity_asset
    subject: InventoryImage
    predicate: creates_or_updates
    object: SanityAsset
  - id: jmd.sanity_asset.used_by_showroom_item
    subject: SanityAsset
    predicate: used_by
    object: ShowroomItem
  - id: jmd.sync_run.records_approval_ledger_entry
    subject: SyncRun
    predicate: creates_or_updates
    object: ApprovalLedgerEntry

state_machines:
  - id: jmd.inventory_image.lifecycle
    entity: InventoryImage
    states:
      raw_uploaded:
        public: false
        next: [needs_review, rejected]
      needs_review:
        public: false
        next: [approved, rejected]
      approved:
        public: false
        next: [scheduled, archived]
      scheduled:
        public: false
        next: [published, archived]
      published:
        public: true
        next: [archived, sold]
      archived:
        public: false
        next: [approved]
      rejected:
        public: false
        next: []
      error:
        public: false
        next: [needs_review]

rules:
  - id: jmd.inventory.raw_uploads_do_not_publish
    title: "Raw uploads do not publish directly"
    status: active
    severity: blocking
    statement: "Images in raw intake must not publish directly to the public website."
    evidence:
      - source_id: jmd-inventory-plan
        lines: "184-202,238-272"
  - id: jmd.inventory.website_reads_sanity_not_drive
    title: "Website reads Sanity, not Drive"
    status: proposed
    severity: blocking
    statement: "The public website should read showroom content from Sanity or another approved backend/CDN, not directly from Google Drive."
    evidence:
      - source_id: jmd-inventory-plan
        lines: "10-18,53-72,166-182"
```

---

## 16. Relationship to existing systems

### 16.1 Obsidian/wiki

The wiki remains a narrative knowledge layer. It explains background, context, decisions, and lessons.

The ontology stores structured facts, entities, rules, and evidence references.

Example split:

```text
Wiki: "Danny is cautious about e-commerce because returns and one-of-a-kind inventory create operational risk."
Ontology: jmd.website.showroom_not_ecommerce = active blocking rule.
```

### 16.2 Skills/procedural instructions

Skills remain procedural. They tell agents/tools how to perform work.

Ontology remains semantic. It tells consumers what exists and what rules govern it.

Example split:

```text
Ontology: Public JMD showroom cards cannot use checkout/live-stock language.
Skill/procedure: Before drafting or reviewing JMD website copy, scan for disallowed terms and verify approved claims.
```

### 16.3 Repos

Repos own implementation-specific docs and code. They should receive projections, not the entire canonical ontology.

Example:

```text
client-ontologies/clients/jmd-menswear/modules/inventory-images.yaml
  ↓ projection
jmd-6-holding-page-harness/docs/ontology/inventory-automation.yaml
  ↓ implementation
Sanity schema + n8n/GitHub/Vercel automation + website rendering
```

### 16.4 Linear/GitHub issues

Project trackers own execution status.

Ontology can be referenced from issues and acceptance criteria.

Example issue section:

```markdown
## Ontology references

- `jmd.inventory.raw_uploads_do_not_publish`
- `jmd.inventory.website_reads_sanity_not_drive`
- `jmd.website.showroom_not_ecommerce`

## Acceptance criteria

- Raw Drive files cannot transition directly to published.
- Website queries Sanity `showroomItem` records only.
- Public copy avoids checkout, live stock, stock count, and guaranteed availability language.
```

---

## 17. Governance workflow

### 17.1 Proposed contribution lifecycle

1. Identify the workstream and client.
2. Inspect current source material: wiki, repo docs, local docs, GitHub, Linear, client approvals.
3. Add or update module facts as `draft` unless already evidence-backed.
4. Add evidence sources and line/path/URL references.
5. Run ontology validator.
6. If public/client-facing, request human review/approval.
7. Mark facts/rules `active` or `approved` only after evidence and approval are recorded.
8. Generate projections/handoff docs.
9. Commit ontology changes with a scoped message.

### 17.2 Commit message examples

```text
docs: add client ontology spec v0.1
feat: add jmd inventory image ontology draft
feat: add femme visibility ontology projection
chore: add ontology validation schema
```

### 17.3 Review checklist

Before merging an ontology change:

- [ ] No secrets or credentials.
- [ ] No unverified public claims marked active/approved.
- [ ] Every public-facing claim has evidence.
- [ ] Every rule has a status and severity.
- [ ] Projections only include relevant slices.
- [ ] Handoff docs are safe for their intended audience.
- [ ] Agent-specific behavior is not embedded in canonical modules.
- [ ] Unknowns remain labeled as unknown instead of normalized silently.

---

## 18. Open decisions

> **Historical pre-implementation decisions — several already settled by shipped
> behaviour.** This section records the original open questions from the pre-implementation
> design. Where a decision has since been resolved by the live implementation, the
> resolution is stated inline and the live contract prevails (see §12, §13, and §19); the
> `Recommended v0` blocks below are the **original** recommendations, kept for history.
> Items that remain genuinely open are marked **Proposed / not yet built**. Live
> sequencing of remaining work is in [`docs/roadmap.md`](roadmap.md).

These predate implementation and should not be read as current contract on their own;
treat each recommendation as historical unless it is confirmed by a live section elsewhere
in this spec.

### 18.1 Canonical repo and projection workflow

Original decision needed:

- Does `client-ontologies` become the only canonical ontology source?
- Do client project folders keep copies?
- Are repo projections generated into implementation repos by script or copied manually at first?

Original recommended v0:

```text
client-ontologies = canonical ontology source
implementation repos = projection consumers
wiki = narrative/context index
```

**Resolution (live):** `client-ontologies` is the canonical source and projections are
curated in-repo consumer views (§11); script-generated projections *into* implementation
repos remain **Proposed** (see [`docs/roadmap.md`](roadmap.md)).

### 18.2 Database/export timing

Original decision needed:

- When does SQLite export become necessary?
- When does Postgres become necessary?

Original recommended v0:

```text
Do not add runtime DB until at least one consumer needs structured lookup beyond file reads.
```

**Resolution (live):** the SQLite runtime export is **shipped** and is the current runtime
projection — `scripts/export_sqlite.py` builds it and §12.2 documents its live schema, so
the "do not add a runtime DB yet" recommendation above is superseded for SQLite. Postgres
remains **Proposed** (§12.3).

### 18.3 Client handoff standard

Original decision needed:

- What qualifies a handoff doc as client-ready?
- Who approves handoff docs before sharing?

Original recommended v0:

```text
Handoff docs are generated/curated from ontology but require human review before sharing externally.
```

**Status (live):** handoff generation/packaging is **Proposed / not yet built** — the
shipped tooling produces no handoff export (§4.1, §13.1). The standing policy that any
handoff must pass human review before external sharing still holds; the open questions
above are tracked in [`docs/roadmap.md`](roadmap.md).

### 18.4 JMD inventory approval mechanism

The inspected JMD inventory plan lists open approval questions:

- Sanity as backend/CMS?
- Approval based on raw Drive age or approved age?
- Approval by Drive folder movement, Sheet status, or Sanity Studio?
- Next/Vercel now or GitHub Actions first?
- Current repo vs real execution repo?

Until those are resolved, the JMD inventory module should remain draft/proposed where the rule depends on those decisions.

---

## 19. Implementation status and forward sequencing

The spec's original phased build-out is **largely delivered**: the split schemas, the
validator, the manifest-first loader, the SQLite exporter, the machine-check engine, the
Femme and JMD v0 ontologies, and their projections all exist today (with a leaner,
workstream-oriented module split than the first sketch proposed). Projection generation
and client-safe handoff packaging remain open.

- The **original phased plan** (with per-phase delivered/superseded annotations) is
  preserved in [`docs/research/initial-ontology-design.md`](research/initial-ontology-design.md).
- The **live, authoritative sequencing** of remaining open work — hard dependencies,
  recommended ordering, and trigger gates — is in [`docs/roadmap.md`](roadmap.md).
  GitHub Issues remain the implementation contracts.

This spec no longer carries a forward execution plan of its own; consult the roadmap.

---

## 20. Glossary

**Client Operating Ontology**  
A structured, version-controlled model of a client's workstreams, entities, relationships, rules, approvals, systems, and handoff artifacts.

**Canonical ontology**  
The reviewed source files in this repo.

**Projection**  
A smaller repo/workflow/handoff-specific slice of the canonical ontology.

**Workstream**  
A category of active or planned client work, such as website, visibility, inventory images, reporting, or CMS content.

**Entity**  
A business object, work product, system resource, content record, workflow artifact, or governance object.

**Relationship**  
A typed connection between two entities.

**Rule**  
A constraint, requirement, approval gate, or public-safety guardrail.

**Evidence source**  
A note, file, repo, issue, URL, API snapshot, or approval record used to justify a fact/rule.

**Handoff export**  
A client- or operator-facing explanation generated or curated from ontology content.

---

## 21. Summary

The v0 ontology should be:

- workstream-first;
- evidence-backed;
- agent-agnostic;
- version-controlled;
- projection-friendly;
- handoff-aware;
- database-exportable later;
- strict about approvals and public claims.

The key architectural choice is to treat the ontology as a **semantic contract above repos and tools**:

```text
Canonical client ontology
  ↓
Repo/workflow/handoff projections
  ↓
Implementation specs, CMS schemas, automation workflows, validation checks
  ↓
Client-safe handoff documentation and reusable client-ontology patterns
```

This keeps the ontology practical for today's Femme/JMD work while preserving a path toward reusable client operating systems later.
