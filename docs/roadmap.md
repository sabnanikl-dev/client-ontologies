# Implementation Roadmap

This roadmap sequences the **open** repository issues in dependency order so work lands
as small, reviewable changes rather than a large undifferentiated batch. It covers the
core runtime path, governance and modeling work, deterministic outcome/competency
testing, the optional LangExtract experiment, and trigger-gated future work.

This file is planning documentation, not canonical ontology truth. GitHub Issues remain
the implementation contracts, and this roadmap must be refreshed whenever the open
issue set or dependency graph changes.

## Delivery policy

Per [`AGENTS.md`](../AGENTS.md):

- Work one issue at a time: one issue, one branch, one focused PR.
- Builders do not self-approve or merge.
- Update the relevant ontology docs in the same PR when concepts, schema, validation,
  export behavior, or consumer semantics change.
- Do not open a stack of implementation PRs merely because several issues are ready.
- Reconstruct current repo and issue state before starting; this file records the
  recommended order, but the assigned issue remains the acceptance contract.

Recommended branch prefixes remain `docs/issue-N`, `ontology/issue-N`,
`schema/issue-N`, `scripts/issue-N`, `fix/issue-N`, or `chore/issue-N`.

## Completed foundations

These issues are **closed and delivered**; they are not active queue work and are listed
only because later issues build on them:

- **#1** — per-client `ontology.yaml` manifests as the reviewable entry point.
- **#2** — split, enforced per-kind JSON Schemas.
- **#3** — CI plus regression tests for validation and SQLite export.
- **#21** — shared manifest-aware loader (`scripts/ontology_loader.py`) used by both the
  validator and exporter.
- **#11** — deterministic `machine_check` copy/safety rule execution (`scripts/check_rules.py`).
- **#31** — the shared competency-question corpus and deterministic outcome runner
  (`tests/competency/questions.yaml` + `tests/run_competency.py`), reused by #19.
- **#19** — the read-only runtime consumer surface: shared transport-agnostic service
  (`scripts/ontology_service.py`) + stdlib CLI (`scripts/ontology_cli.py`), YAML and
  SQLite backends, `ontology` / `ontology-mcp` console entry points (`pyproject.toml`).
- **#25** — the `metric` entity type modeled against real Femme local-visibility outcomes
  (`draft`/`baseline: unknown` where no snapshot exists).
- **#26** — corrected agent-facing `CLAUDE.md` guidance.

## Hard dependency map

Delivered prerequisites are marked `(done)`; they still anchor the graph because open
work depends on them.

```text
#21 shared loader (done) -> #11 machine checks (done) -> #19 runtime surface (done)
#21 shared loader (done) -> #31 competency corpus (done) .. reused by #19 runtime surface (done)

#9 approval gates -> #8 provenance
#9 approval gates -> #4 actions -> #10 state-machine guards
#9 approval gates ---------------------> #10 state-machine guards

#27 optional LangExtract toolchain -> #28 bounded pilot

#5 interfaces/shared properties -> #12 handoffs -> #6 lifecycle/cleanup

#19 runtime/MCP surface + #31 competency corpus -> explicit prerequisites to #7 activation
    (activation also requires a measured full-load/filtered-SQLite failure + sanitized
     gold source highlights; these gate activation, they do not authorize implementation)
```

Issues #22, #23, and #24 have no hard implementation dependency, but their placement
below avoids rework and reduces risk for later agents.

**Soft sequencing, not hard dependencies:**

- **#31** (competency-question corpus) landed *before #19 closed*, as recommended, so the
  runtime surface proves normalized YAML/SQLite answer parity against the shared corpus and
  #19 reuses it (both now delivered). This was a recommended sequencing/reuse gate, not a
  code-level hard dependency.
- **#24** (new-client scaffolding) and **#5** (interfaces/shared properties) are linked
  only by soft, recommended sequencing. #24 is independent and recommended before
  onboarding the next client, while #5 is trigger-gated: it starts when a third client
  lands *or* concrete cross-client duplication/God-object pain appears. Onboarding a
  third client does not automatically require #5.

## Recommended execution queue

The queue below lists only **open** issues. The documentation-normalization issue **#32**
(separating the live contract from design history and refreshing this roadmap) is the
current Phase 0 work and is described under Phases below.

| Order | Issue | Work | Why here / gate |
|---:|---:|---|---|
| ✅ | **#31** *(delivered)* | Competency-question traceability and deterministic semantic outcome tests | Delivered. Shared outcome-usefulness corpus (test metadata, not a canonical kind); reused by #19 to prove YAML/SQLite answer parity. |
| ✅ | **#19** *(delivered)* | Read-only runtime core and CLI | Delivered. Clients, context, projections, rules, and copy checks through one shared transport-agnostic service; YAML and SQLite backends; `ontology`/`ontology-mcp` entry points. Reuses #31's corpus to prove consumer operations. |
| 1 | **#9** | Make approval gates and records first-class | Governance foundation; blocks #4 and the approval-guard portion of #10. |
| 4 | **#8** | Add projection provenance and runtime build metadata | Follows #9 in the governance layer and lets consumers identify the ontology state behind projections and exports. |
| 5 | **#23** | Add portable evidence anchors and evidence-health reporting | Completes the provenance/evidence integrity layer without conflating citation health with resource lifecycle. |
| 6 | **#22** | Constrain relationship predicates, cardinality, and inverse names | Stabilize relationship semantics before broader model expansion (the delivered #25 metric work already exercises predicates such as `measures`). |
| 7 | **#4** | Model actions, functions, and agent-exposed operations | Requires #9. Modeling an operation makes it discoverable, never automatically executable. |
| 8 | **#10** | Validate/export state machines and add transition guards | Planned after #4; guard behavior requires #9. Validation/export may be split first only if #9 is unexpectedly delayed. |
| 9 | **#24** | Add deterministic new-client scaffolding | Reuse #21's loader and land before onboarding a third real client. |
| 10 | **#27** | Add the isolated optional LangExtract toolchain | Begins an optional experimental lane without adding dependencies to canonical validation/export. |
| 11 | **#28** | Pilot source-grounded candidate extraction on Femme and JMD fixtures | Hard-blocked by #27; must end in a measured continue, narrow, or stop decision and must not write canonical truth automatically. |
| 12 | **#5** | Add interfaces and shared properties | Trigger-gated: start only when a third client lands or concrete duplication/God-object pain appears. |
| 13 | **#12** | Generate client-safe handoff packages | Planned after #5; benefits from #8 provenance and #21's loader even though neither is a strict technical blocker. |
| 14 | **#6** | Add lifecycle, impact, deprecation, and cleanup workflows | Last in the planned #5 -> #12 -> #6 modeling-hygiene sequence. Keep separate from #23's evidence-health checks. |
| 15 | **#7** | Represent semantic retrieval resources | Explicitly last and speculative; **DO NOT BUILD YET**. Activation requires #19's runtime/CLI surface **and** #31's competency-question corpus as explicit prerequisites, plus a measured full-load/filtered-SQLite failure and sanitized gold source highlights — an evidence/benchmark gate, not an implementation authorization. Reassess after #19/MCP exists; close or replace it if retrieval belongs in the service layer rather than canonical projection YAML. |

## Pull-forward rules

The table is the default queue, not a reason to ignore changed business context:

- If source-grounded intake is the active experiment, **#27 -> #28** may move earlier in
  the queue. Keep both PRs isolated from the canonical runtime dependency path.
- If a third client is imminent, move **#24** forward. Start **#5** only after onboarding
  exposes actual shared-property or interface pressure.
- Independent quality work **#24** may fill a deliberate gap, but should not delay the
  active governance path without a concrete reason. (The #19 runtime path is delivered.)
- Do not start **#4** or approval-guarded **#10** work before #9.
- #19 closed only after **#31**'s corpus could exercise its consumer operations — the
  runtime surface reuses that corpus to prove YAML/SQLite answer parity (both delivered).
- Do not pull **#7** forward while full-projection loading remains sufficient at current
  scale.

## Phases and completion gates

### Phase 0 — Agent hygiene, a small authoring proof, and a current contract

Issues: **#26 (done) -> #25 (done) -> #32 (current)**

Exit gate:

- Agent-facing guidance matches the current manifest, schema, validator, export, test,
  and CI reality. *(#26, delivered.)*
- The `metric` resource type has one validator-compliant use — modeled honestly against
  the evidence that exists (`draft`/`unknown` where the source names no baseline or
  snapshot, evidence-cited where it does). *(#25, delivered.)*
- `docs/spec.md` is normative and current, design history and the original source
  inventory are relocated under `docs/research/`, future ideas are marked
  proposed/trigger-gated, this roadmap tracks only live open issues, and all agent-facing
  contract surfaces agree on the four resource kinds, live paths, and commands.
  *(#32, the current documentation-only work.)*

### Phase 1 — Shared foundations, outcome corpus, and runtime v1

Issues: **#21 (done) -> #11 (done) -> #31 (done) -> #19 (done)**

Exit gate:

- Validator, exporter, and new consumers share one manifest-aware loader. *(#21, delivered.)*
- Machine checks have deterministic positive and negative coverage. *(#11, delivered.)*
- A competency-question corpus deterministically proves consumers still get correct,
  status-aware answers, with a controlled negative case for semantic drift. *(#31, delivered.)*
- A read-only CLI exposes the agreed v1 operations without granting mutation authority,
  reusing the competency corpus to prove YAML/SQLite answer parity. *(#19, delivered.)*

### Phase 2 — Governance and semantic integrity

Issues: **#9 -> #8 -> #23 -> #22**

Exit gate:

- Approval gates, scoped approval records, provenance, evidence health, and relationship
  semantics are machine-checkable and exported where specified.
- Approval records remain evidence of one scoped past approval, never standing authority
  for future actions.

### Phase 3 — Kinetic and workflow semantics

Issues: **#4 -> #10**

Exit gate:

- Actions and state transitions are queryable, validated, and approval-aware.
- Defining or exposing an action does not authorize a runtime mutation.
- Any MCP `list_actions` or `check_action_allowed` extension remains read-only unless a
  separately approved runtime authority design exists.

### Phase 4 — Onboarding and bounded intake experiments

Issues: **#24**, then optional **#27 -> #28**

Exit gate:

- A third client can be scaffolded deterministically without copying an existing client.
- If the LangExtract lane is run, its report records misses, false positives, reviewer
  effort, cost visibility, security observations, and an explicit continue/narrow/stop
  decision.

### Phase 5 — Trigger-gated modeling maturity

Issues: **#5 -> #12 -> #6**

Start gate:

- A third client exists or concrete cross-client duplication makes interfaces/shared
  properties useful now.

Exit gate:

- Shared concepts are modeled without premature taxonomy sprawl.
- Handoff exports are client-safe and provenance-aware.
- Lifecycle and cleanup reports identify stale, deprecated, or orphaned resources without
  deleting anything automatically.

### Phase 6 — Speculative retrieval and later adapters

Issue: **#7**, plus any separately filed HTTP adapter work.

Before implementing #7, compare its proposed projection metadata with the runtime/MCP
surface delivered by #19. Current default retrieval remains full projection loading;
semantic retrieval is opt-in only when scale or a real consumer requires it. Retrieved
snippets are context, never evidence for a verified claim.

Per issue #7, #7 is explicitly **DO NOT BUILD YET**. Its live activation gate makes both
**#19** (runtime/CLI surface, to measure current consumer behavior) and **#31** (the
competency-question corpus that defines the consumer questions retrieval must support)
explicit prerequisites to activation — not merely related issues. Activation additionally
requires a real consumer demonstrating a measured full-load or filtered-SQLite failure
(accuracy, context-budget, or latency) and sanitized gold source highlights identifying
the exact source spans. This is an evidence/benchmark gate on *when the work may start*;
it does not authorize implementation, and it is distinct from #31's soft
sequencing/reuse relationship with #19 recorded above.

## Runtime consumer surface shape

One shared, stdlib-first core with thin adapters keeps transport choices additive:

```text
scripts/ontology_loader.py   -- load + resolve projections (stdlib, #21, delivered)
scripts/check_rules.py       -- machine_check engine (stdlib, #11, delivered)
scripts/ontology_service.py  -- transport-agnostic operations, plain JSON dicts (#19, delivered)
        |
        +-- scripts/ontology_cli.py       <- v1: CI / hooks / local consumers (#19, delivered)
        +-- server/ontology_mcp.py        <- next: thin isolated MCP adapter
        +-- server/ontology_api.py        <- later: separately scoped HTTP adapter
```

Runtime v1 is read-only and **delivered** (#19): the shared core + CLI, YAML and SQLite
backends, and the `ontology` / `ontology-mcp` console entry points (`pyproject.toml`). The
CLI, the future MCP adapter, and the shared core live in this repository, co-located with
the schema and data they interpret. Consumers install or register that implementation
rather than reimplementing parser and guardrail logic downstream. The `ontology-mcp` entry
point is registered for packaging completeness; the MCP stdio adapter itself
(`server/ontology_mcp.py`) is the next PR.

For an agentic-harness consumer such as Femme Visibility:

- Pin the in-repo consumer package by tag or commit for provenance.
- Use MCP as an agent-facing query surface when appropriate.
- Use the CLI as the deterministic CI or pre-publication enforcement surface.
- Consume the generated SQLite projection when a Ruby-free runtime path is required.
- Keep YAML authoring and canonical validation in this repository.

## Remaining untracked design gap

The currently open issues cover new-client scaffolding (#24), governance and provenance
(#9, #8), actions and state machines (#4, #10), handoff and lifecycle hygiene (#12, #6),
interfaces (#5), the LangExtract experiment (#27–#28), and speculative retrieval (#7).
Deterministic outcome/competency testing (#31), the runtime surface (#19), relationship
semantics (#22), evidence portability (#23), metric modeling (#25), and agent-doc drift
(#26) are delivered; this roadmap normalization is #32.

One material gap remains only partially covered: typed properties with per-property
evidence and confidence. Issue #5 provides a possible extension point, but a separate
issue should be filed only after real client data demonstrates that free-form entity
fields are causing review or consumer failures.
