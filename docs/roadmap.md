# Implementation Roadmap

This roadmap sequences the open repository issues in dependency order so work lands as
small, reviewable changes rather than a large undifferentiated batch. It covers the
core runtime path, governance and modeling work, the optional LangExtract experiment,
and trigger-gated future work.

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

## Hard dependency map

```text
#21 shared loader -> #11 machine checks -> #19 runtime surface

#9 approval gates -> #8 provenance
#9 approval gates -> #4 actions -> #10 state-machine guards
#9 approval gates ---------------------> #10 state-machine guards

#27 optional LangExtract toolchain -> #28 bounded pilot

#5 interfaces/shared properties -> #12 handoffs -> #6 lifecycle/cleanup

#19 runtime/MCP design -> reassess #7 retrieval contract
```

Issues #22, #23, #24, #25, and #26 have no hard implementation dependency, but their
placement below avoids rework and reduces risk for later agents.

#24 (new-client scaffolding) and #5 (interfaces/shared properties) are linked only by
soft, recommended sequencing — not a hard dependency. #24 is independent and recommended
before onboarding the next client, while #5 is trigger-gated: it starts when a third
client lands *or* concrete cross-client duplication/God-object pain appears. Onboarding a
third client does not automatically require #5.

## Recommended execution queue

| Order | Issue | Work | Why here / gate |
|---:|---:|---|---|
| 1 | **#26** | Correct stale `CLAUDE.md` agent guidance | Fix misleading repo instructions before assigning implementation work to builders. |
| 2 | **#25** | Model measurable outcomes with the `metric` entity type | Small ontology-authoring warm-up; establishes real metric relationships before #22 freezes a controlled predicate vocabulary. |
| 3 | **#21** | Consolidate YAML parsing and make export manifest-aware | Shared technical foundation; hard prerequisite for #11 and #19 and reusable by later scripts. |
| 4 | **#11** | Implement machine-checkable copy and safety rules | Consumes #21 and supplies the `machine_check` engine required by #19. |
| 5 | **#19** | Deliver the read-only runtime core and CLI | First major consumer value: clients, context, projections, rules, and copy checks through one shared service layer. |
| 6 | **#9** | Make approval gates and records first-class | Governance foundation; blocks #4 and the approval-guard portion of #10. |
| 7 | **#8** | Add projection provenance and runtime build metadata | Follows #9 in the governance layer and lets consumers identify the ontology state behind projections and exports. |
| 8 | **#23** | Add portable evidence anchors and evidence-health reporting | Completes the provenance/evidence integrity layer without conflating citation health with resource lifecycle. |
| 9 | **#22** | Constrain relationship predicates, cardinality, and inverse names | Land after #25 exposes any real metric predicate such as `measures`; stabilize relationship semantics before broader model expansion. |
| 10 | **#4** | Model actions, functions, and agent-exposed operations | Requires #9. Modeling an operation makes it discoverable, never automatically executable. |
| 11 | **#10** | Validate/export state machines and add transition guards | Planned after #4; guard behavior requires #9. Validation/export may be split first only if #9 is unexpectedly delayed. |
| 12 | **#24** | Add deterministic new-client scaffolding | Reuse #21's loader and land before onboarding a third real client. |
| 13 | **#27** | Add the isolated optional LangExtract toolchain | Begins an optional experimental lane without adding dependencies to canonical validation/export. |
| 14 | **#28** | Pilot source-grounded candidate extraction on Femme and JMD fixtures | Hard-blocked by #27; must end in a measured continue, narrow, or stop decision and must not write canonical truth automatically. |
| 15 | **#5** | Add interfaces and shared properties | Trigger-gated: start only when a third client lands or concrete duplication/God-object pain appears. |
| 16 | **#12** | Generate client-safe handoff packages | Planned after #5; benefits from #8 provenance and #21's loader even though neither is a strict technical blocker. |
| 17 | **#6** | Add lifecycle, impact, deprecation, and cleanup workflows | Last in the planned #5 -> #12 -> #6 modeling-hygiene sequence. Keep separate from #23's evidence-health checks. |
| 18 | **#7** | Represent semantic retrieval resources | Explicitly last and speculative. Reassess after #19/MCP exists; close or replace it if retrieval belongs in the service layer rather than canonical projection YAML. |

## Pull-forward rules

The table is the default queue, not a reason to ignore changed business context:

- If source-grounded intake is the active experiment, **#27 -> #28** may move directly
  after #21. Keep both PRs isolated from the canonical runtime dependency path.
- If a third client is imminent, move **#24** directly after #21. Start **#5** only after
  onboarding exposes actual shared-property or interface pressure.
- Independent quality work **#22**, **#23**, and **#24** may fill a deliberate gap, but
  should not delay the #21 -> #11 -> #19 path without a concrete reason.
- Do not start **#4** or approval-guarded **#10** work before #9.
- Do not start **#19** before both #21 and #11.
- Do not pull **#7** forward while full-projection loading remains sufficient at current
  scale.

## Phases and completion gates

### Phase 0 — Agent hygiene and a small authoring proof

Issues: **#26 -> #25**

Exit gate:

- Agent-facing guidance matches the current manifest, schema, validator, export, test,
  and CI reality.
- The existing `metric` resource type has one evidence-backed, validated use rather than
  remaining an unused schema possibility.

### Phase 1 — Shared foundations and runtime v1

Issues: **#21 -> #11 -> #19**

Exit gate:

- Validator, exporter, and new consumers share one manifest-aware loader.
- Machine checks have deterministic positive and negative coverage.
- A read-only CLI exposes the agreed v1 operations without granting mutation authority.

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

## Runtime consumer surface shape

One shared, stdlib-first core with thin adapters keeps transport choices additive:

```text
scripts/ontology_loader.py   -- load + resolve projections (stdlib, #21)
scripts/check_rules.py       -- machine_check engine (stdlib, #11)
scripts/ontology_service.py  -- transport-agnostic operations, plain JSON dicts (#19)
        |
        +-- scripts/ontology_cli.py       <- v1: CI / hooks / local consumers
        +-- server/ontology_mcp.py        <- next: thin isolated MCP adapter
        +-- server/ontology_api.py        <- later: separately scoped HTTP adapter
```

Runtime v1 is read-only. The CLI, MCP adapter, and shared core live in this repository,
co-located with the schema and data they interpret. Consumers install or register that
implementation rather than reimplementing parser and guardrail logic downstream.

For an agentic-harness consumer such as Femme Visibility:

- Pin the in-repo consumer package by tag or commit for provenance.
- Use MCP as an agent-facing query surface when appropriate.
- Use the CLI as the deterministic CI or pre-publication enforcement surface.
- Consume the generated SQLite projection when a Ruby-free runtime path is required.
- Keep YAML authoring and canonical validation in this repository.

## Remaining untracked design gap

The currently known open issues cover relationship semantics (#22), evidence portability
(#23), new-client scaffolding (#24), metric modeling (#25), agent-doc drift (#26), and
the LangExtract experiment (#27–#28).

One material gap remains only partially covered: typed properties with per-property
evidence and confidence. Issue #5 provides a possible extension point, but a separate
issue should be filed only after real client data demonstrates that free-form entity
fields are causing review or consumer failures.
