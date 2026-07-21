# Implementation Roadmap

> **Status:** live planning document for the open GitHub issue set.
> **Canonical truth:** client YAML under `clients/`; this roadmap is not ontology data.
> **Last reconstructed against:** `main` at `e1fdeb450ffc28b2348bfcbfcdf64f72e14fe130` and the open issue set through #44.

This roadmap sequences work toward an evidence-backed **client operating ontology** that contains durable, relevant business and technical semantics and can be queried safely by agents, scripts, UIs, and future adapters.

The CLI's copy checker is one enforcement operation—not the product boundary. The target system must support six distinct capabilities:

1. **canonical modeling and client coverage** — what exists, what it means, how it relates, and what evidence supports it;
2. **competency and usefulness** — which business/technical questions the ontology must answer and whether answers stay correct;
3. **read-only runtime consumption** — projection-scoped access to entities, relationships, modules, rules, approvals, actions, and state transitions;
4. **governance and kinetic semantics** — approval gates, action requirements, and guarded transitions without granting execution authority;
5. **reviewed source intake** — collection, classification, reconciliation, human review, and safe patch proposals;
6. **speculative retrieval/adapters** — GraphRAG/vector retrieval, MCP, or HTTP only when separately justified and tracked.

GitHub Issues remain the implementation contracts. Refresh this file whenever the open issue set, dependencies, or trigger gates change.

## Core boundary

The ontology should contain durable semantics:

- business definitions, offerings, roles, ownership, and operating constraints;
- systems, repositories, domains, environments, systems of record, integrations, and data-flow meaning;
- relationships, policies, metric definitions, lifecycle, actions, approvals, and state transitions;
- status, confidence, provenance, and evidence.

It should **reference rather than duplicate** raw CRM leads, inventory instances, analytics events, CMS bodies, GitHub/Linear task state, private exports, credentials, or secrets. If a datum defines how the business interprets or governs an event, it may belong canonically; if it is merely one operational instance, it belongs in its source system.

## Three maturity dimensions

Do not conflate these:

1. **Model representation** — the schema can describe a concept.
2. **Client coverage** — a specific client ontology actually contains sufficient evidence-backed resources to answer the needed question.
3. **Runtime queryability** — a supported consumer can retrieve that answer with correct scope, status, evidence, and provenance.

A table in SQLite is not automatically a usable consumer surface. A schema field is not proof that current client coverage is sufficient.

## Delivery policy

Per [`AGENTS.md`](../AGENTS.md):

- one issue, one branch, one focused PR;
- builders do not self-approve or merge;
- update relevant docs in the same PR when concepts, schema, validation, export, or consumer semantics change;
- canonical YAML remains reviewed truth; generated SQLite and intake staging remain non-canonical;
- no issue grants deploy, account mutation, publishing, client-facing send, or automatic canonical-write authority;
- reconstruct live repo, issue, PR, and dependency state before starting work;
- keep hard dependencies separate from recommended sequencing and trigger gates.

Recommended branch prefixes remain `docs/issue-N`, `ontology/issue-N`, `schema/issue-N`, `scripts/issue-N`, `fix/issue-N`, or `chore/issue-N`.

## Completed foundations

These delivered issues anchor the open work:

- **#1** — per-client `ontology.yaml` manifests.
- **#2** — split, enforced schemas by resource kind.
- **#3** — CI and regression tests for validation/SQLite export.
- **#21** — shared manifest-aware YAML loader.
- **#11** — deterministic machine-check rule execution.
- **#22** — controlled relationship predicates, cardinality, inverse vocabulary, and bounded domain/range checks.
- **#23** — portable evidence anchors and evidence-health reporting.
- **#25** — metric entity proof against real Femme outcome definitions.
- **#26** — corrected agent-facing orientation.
- **#31** — projection-scoped competency-question corpus and deterministic outcome runner.
- **#32** — normative spec/history separation and prior roadmap normalization.
- **#19** — shared read-only runtime service + CLI with YAML/SQLite parity, projection isolation, and five v1 operations.

Runtime v1 is a foundation, not the final query surface. It currently exposes clients, entity/rule context, rules, copy checks, and projections; relationships and later approval/action/state resources need the open work below.

## Live open-issue coverage

Every currently open issue is listed here.

| Issue | Track | Role / current gate |
|---:|---|---|
| **#44** | Roadmap | This docs-only refresh; closes through the roadmap PR and changes no implementation/canonical data. |
| **#40** | Reliability | Prove installed entry points at Python 3.10 and synchronize README maps/tests. Independent and first in queue. |
| **#41** | Coverage + competency | Define mature-client business/technical question families and add relationship/multi-hop deterministic competency contracts. |
| **#42** | Runtime consumption | Add projection-scoped entity, relationship, module, and workstream queries over the shared YAML/SQLite service. Depends on #41. |
| **#9** | Governance | First-class approval gates/records plus read-only runtime access. Runtime portion follows #42. |
| **#8** | Provenance | Projection/export provenance, build metadata, and versioned SQLite artifact distribution. Recommended after #9 in the governance sequence. |
| **#4** | Kinetic semantics | Evidence-backed actions, risk/side effects/preconditions, governing gates, and read-only requirement evaluation. Depends on #9; runtime extension uses #42. |
| **#10** | Workflow semantics | Validate/export/query state machines and approval-gated public-boundary transitions. Guard layer depends on #9 and follows #4. |
| **#24** | Onboarding | Deterministic draft-by-default scaffold before the next real client. Independent; pull forward if onboarding is imminent. |
| **#43** | Intake | Parent tracker for collect → normalize → classify/sanitize → reconcile → review → propose → verify. Begins from #41's salient-question contract. |
| **#27** | Optional intake experiment | Isolated LangExtract toolchain only; one extractor lane under #43, not the intake architecture. |
| **#28** | Optional intake experiment | Sanitized source-grounded extraction pilot; hard-blocked by #27 and must end continue/narrow/stop. |
| **#5** | Trigger-gated modeling | Shared interfaces/properties only after a third client or demonstrated duplication/God-object pain. |
| **#12** | Trigger-gated handoff | Client-safe Markdown handoff generation; recommended after #5 and benefits from #8, but generation never permits sending. |
| **#6** | Trigger-gated maintenance | Lifecycle, deprecation, impact, and health reporting; recommended after #5/#12. |
| **#7** | Speculative retrieval | **DO NOT BUILD YET.** Requires #41/#42 plus a measured structured-query failure and benchmark win without weaker correctness/traceability/privacy. |

## Hard dependency map

Arrows below mean an issue cannot satisfy its full current acceptance contract before the prerequisite lands. Trigger gates and recommended ordering are listed separately.

```text
#31 competency foundation (done) -> #41 coverage + multi-hop competency
#19 runtime foundation (done) ----> #42 broad read-only queries
#41 ------------------------------> #42
#41 ------------------------------> #43 intake architecture

#42 runtime extension pattern ----> #9 approval runtime integration
#9 approval gates ----------------> #4 actions / requirement evaluation
#9 approval gates ----------------> #10 transition guards
#4 actions -----------------------> #10 guarded state transitions

#27 optional LangExtract toolchain -> #28 bounded extraction pilot

#41 + #42 + measured structured-query failure
    + sanitized benchmark/gold spans -> #7 activation review
```

Issue #24 has no implementation dependency. Issue #5 is not a hard dependency of onboarding: it activates only after a third client or real duplication pressure appears.

## Recommended sequencing that is not a hard dependency

- **#9 → #8** keeps approval governance and runtime provenance together; #8 has independent implementation seams but should not displace the active coverage/query path.
- **#5 → #12 → #6** remains a maturity sequence, not permission to build #5 before its trigger.
- Client-specific coverage follow-ups discovered by #41 may run after #41 as focused one-client PRs. They should use current entity/relationship/field shapes first and create new kinds/interfaces only when evidence shows model pressure.
- #43 is a parent tracker. Its normalized-source, checkpoint, classification, reconciliation, review-packet, and patch-proposal slices require separate child issues/PRs.
- #27/#28 may move earlier only when the optional extraction experiment is the explicit active priority. They do not block deterministic intake-contract work that has no LangExtract dependency.

## Recommended execution queue

| Order | Issue | Outcome / gate |
|---:|---:|---|
| Current | **#44** | Refresh this roadmap only; open PR, do not merge without separate approval. |
| 1 | **#40** | Lock installed CLI reliability and contributor docs before adding commands. |
| 2 | **#41** | Define coverage/usefulness through deterministic business, technical, relationship, and multi-hop competency questions. |
| 3 | **#42** | Make current entity/relationship/module semantics queryable through the supported service/CLI. |
| 4 | **#9** | Structure and expose approval gates/records without creating standing authority. |
| 5 | **#8** | Add projection/export provenance and a pinned SQLite distribution contract. |
| 6 | **#4** | Model and query action requirements after gates exist; no executor. |
| 7 | **#10** | Validate/export/query state machines and guarded public-boundary transitions. |
| 8 | **#24** | Make the next client scaffold deterministic and draft by default. |
| 9 | **#43** | Activate the intake parent and file focused foundation child issues. |
| 10 | **#27** | Install/smoke the optional extractor lane without changing canonical runtime dependencies. |
| 11 | **#28** | Measure extraction on sanitized Femme/JMD fixtures and decide continue/narrow/stop. |
| 12 | **#5** | Trigger only after demonstrated shared-property/interface pressure. |
| 13 | **#12** | Generate client-safe, provenance-aware handoff drafts after maturity trigger. |
| 14 | **#6** | Add lifecycle/deprecation/impact health after the model/handoff surface stabilizes. |
| 15 | **#7** | Last/speculative; build only after its strengthened activation gate passes. |

## Pull-forward rules

- If a third client is imminent, pull **#24** forward. Do not automatically activate #5 unless onboarding demonstrates duplication or inconsistent fields.
- If existing client work exposes an evidence-backed coverage gap after #41, create one focused client/module issue; do not bundle unrelated clients or invent facts to make the matrix green.
- If source intake is the active mission, activate **#43** and create its normalized-source/reconciliation child contracts before choosing a production extraction model.
- If #27/#28 run early, preserve their isolation: no connector authority, canonical writes, auto-promotion, or GraphRAG.
- Do not start #4 before #9. Do not complete #10's guard layer before #9/#4.
- Do not activate #7 while #41/#42's deterministic full-load/SQLite modes answer the required questions adequately.

## Phases and completion gates

### Phase 0 — Roadmap and installed-runtime reliability

Issues: **#44 → #40**

Exit gate:

- every live open issue is represented without fabricated dependencies;
- installed `ontology`/`ontology-mcp` entry points are tested at the declared Python floor;
- README map/test commands match the shipped runtime;
- no MCP implementation is implied by the placeholder entry point.

### Phase 1 — Coverage specification and broad read-only consumption

Issues: **#41 → #42**

Exit gate:

- coverage families distinguish model capability, actual client coverage, and runtime queryability;
- each client has relationship-backed business and technical competency questions;
- at least one bounded multi-hop question is deterministic and projection isolated;
- installed/service consumers can list/get scoped entities, relationships, modules, and workstreams with YAML/SQLite parity;
- relationships remain modeled/queryable before any semantic-retrieval work.

### Phase 2 — Governance, provenance, actions, and state transitions

Recommended sequence: **#9 → #8 → #4 → #10**

Exit gate:

- approval gates/records are validated, exported, queryable, and never treated as standing authority;
- consumers can identify the ontology/build state behind projections and SQLite snapshots;
- actions expose requirements/risk/side effects without execution authority;
- state transitions, public boundaries, and approval guards are validated and queryable;
- #41 competency outcomes cover the new resource types without duplicating expected answers in service code.

### Phase 3 — Client onboarding and reviewed intake

Issues: **#24**, then parent **#43** with focused children; optional **#27 → #28**

Exit gate:

- a third client can be scaffolded without copied facts and validates draft by default;
- intake sources normalize into privacy/authority-classified records outside canonical YAML;
- comments/material are classified before fact extraction;
- candidates reconcile as matching/new/changed/conflicting/stale/rejected with source anchors;
- unresolved conflicts remain review staging, not silently chosen canonical truth;
- review packets and patch proposals fail closed and require a human-reviewed PR;
- if LangExtract is tested, the report records misses, false positives, exact-span quality, reviewer effort, cost/security observations, and continue/narrow/stop.

### Phase 4 — Trigger-gated modeling maturity and handoff

Issues: **#5 → #12 → #6** only after trigger

Start gate:

- a third client or concrete cross-client duplication/consumer failure proves shared interfaces/properties are useful now.

Exit gate:

- shared concepts reduce proven drift without taxonomy/God-object sprawl;
- client-safe handoff drafts are provenance-aware and human-gated;
- lifecycle/health reports identify deprecated, stale, or orphaned resources without deleting automatically.

### Phase 5 — Speculative retrieval and later adapters

Issue: **#7**; MCP/HTTP require separately filed issues.

Before #7 can start, #41/#42 must establish and measure the deterministic baseline. A real consumer must show an explicit correctness, context-budget, or latency failure; sanitized source highlights and competency assertions must define the benchmark; semantic/hybrid retrieval must materially beat the activating baseline without weakening answer correctness, projection isolation, evidence traceability, or privacy.

If the structured modes remain sufficient, keep `full_load`/filtered SQLite and close or continue deferring #7. GraphRAG/vector retrieval is not a substitute for modeling relationships or exposing them through the service.

The `ontology-mcp` entry point remains a fail-closed placeholder. There is currently no open MCP implementation issue, so this roadmap does not call it “the next PR.” Any future MCP or HTTP adapter must be thin, read-only by default, reuse `ontology_service.py`, and preserve the same competency/isolation contracts.

## Remaining design gaps and decision rules

### Client-specific coverage gaps

#41 should reveal these through a matrix and failed/unanswered competency questions. File focused, evidence-backed client issues only after the gap is observed. Do not pre-create one module per category or force every category onto every client.

### Typed properties and per-property evidence

`fields` remains intentionally flexible at two clients. #5 provides an extension point for shared properties/interfaces, but typed per-property confidence/evidence deserves a separate issue only after concrete field drift, review ambiguity, UI-form pressure, or competency failure demonstrates the need.

### Intake conflict representation

#43 stages divergent claims with source/temporal context for human reconciliation. Do not add a canonical `conflicted` status or generic automatic contradiction detector until repeated real cases prove unresolved conflict is durable ontology truth rather than intake state.

### Technical architecture modeling

Use current evidence-backed entities (`system_resource`, `workflow_artifact`, business/governance objects), relationships, and fields for repositories, domains, applications, stores, integrations, ownership, and data flows. Add new canonical kinds only when current shapes fail explicit competency or consumer requirements.

## Roadmap maintenance check

Before merging a roadmap refresh:

1. query the live open issue set;
2. verify every open issue number appears explicitly in this document;
3. separate hard dependencies from recommended sequence and activation triggers;
4. ensure closed foundations are not presented as active work;
5. verify no stale “next PR” or untracked implementation claim remains;
6. keep the PR roadmap-only and run the canonical validation/export/test suite.
