# Client Ontology Agent Harness

*Slim process bible. All agents must read this before touching the repo.*

This repository is the canonical, agent-agnostic source of truth for client operating ontologies. Treat it like infrastructure: small diffs, explicit evidence, deterministic validation, and human approval for authority-changing work.

## Roles

| Agent | Role |
|-------|------|
| **Human** | Owns client truth, taste, and approval boundaries. Approves public/client-facing or authority-changing changes. |
| **Orchestrator** | Reads issues, reconstructs repo state, scopes work, coordinates builders/reviewers, and reports verified outcomes. |
| **Ontology Builder** | Edits YAML/docs/schemas/scripts for one issue at a time. Does not self-approve. |
| **Ontology Reviewer** | Reviews schema safety, evidence quality, references, validation/export behavior, and handoff safety. |
| **Consumer/App Builder** | Uses projections/exports from this repo. Does not redefine canonical ontology truth in downstream repos. |

## Core Rules

1. **Canonical truth lives here.** Repo-local ontology copies in client/app repos are projections, not source of truth, unless explicitly declared otherwise.
2. **Evidence beats memory.** Durable client facts, rules, relationships, and public claims need source-backed evidence or must be marked `draft`, `inferred`, or `unknown`.
3. **One issue, one branch, one PR.** Do not bundle unrelated clients, modules, schema changes, and generated exports unless the issue explicitly requires it.
4. **Generation ≠ evaluation.** Builders do not review their own ontology changes.
5. **Human gate is load-bearing.** No publishing, account mutation, client-facing handoff, or authority expansion without explicit human approval.
6. **No secrets or raw private exports.** Never commit credentials, OAuth tokens, private keys, raw client exports, payment data, or unnecessary PII.
7. **If it is not in the repo, cite it.** External knowledge can inform work, but ontology claims must point to a repo file, issue, URL, approval record, or documented source.

## Session Start (Orientation)

Every session begins with explicit state reconstruction:

```bash
1. git status --short --branch
2. Read AGENTS.md
3. Read README.md, docs/spec.md, and docs/conventions.md
4. Read the assigned GitHub issue and acceptance criteria
5. Inspect the relevant client/module/projection files
6. git log --oneline -20
7. Run python3 scripts/validate_ontology.py before editing when practical
```

**Fresh session every time.** Do not rely on prior chat context or `--continue`. Reconstruct from files and the assigned issue.

## Repository Map

```text
AGENTS.md                    # This file — process rules for agents
README.md                    # Repo purpose, map, quickstart, validation/export commands
docs/
  spec.md                    # Client Operating Ontology spec
  conventions.md             # IDs, statuses, evidence, approval, module boundaries
  examples.md                # Consumer examples and query patterns
schemas/                     # JSON Schema contracts
scripts/
  validate_ontology.py       # Deterministic validation gate
  export_sqlite.py           # Optional runtime projection export
clients/<client-slug>/
  client.yaml                # Client-level metadata, sources, privacy posture
  modules/*.yaml             # Canonical entities, relationships, rules, workflows
  projections/*.yaml         # Curated views for consumers/agents/apps
```

## Ontology Authoring Rules

- Model **real-world client concepts**, not source-system tables or agent-specific convenience objects.
- Keep modules small and workstream-oriented: brand, website, local visibility, operations, inventory, handoff, etc.
- Use stable, lowercase, namespaced IDs: `client-slug.workstream.object-name`.
- Do not encode temporary tracker state in ontology IDs: no issue numbers, PR numbers, sprint names, or current task status.
- Separate canonical truth from projections:
  - `clients/*/modules/*.yaml` = canonical facts/rules/relationships.
  - `clients/*/projections/*.yaml` = curated consumer views.
  - generated SQLite/build outputs = runtime projections only.
- Mark uncertainty honestly: `verified`, `owner_reviewed`, `inferred`, `draft`, or `unknown`.
- Prefer explicit relationships over hiding semantics inside prose fields.
- Approval rules and authority boundaries belong in operations/governance modules, not buried in brand voice or website copy.

## Evidence & Source Rules

- Every active/approved public-facing entity, relationship, rule, claim, approval gate, or handoff item needs evidence.
- Evidence `source_id`s are local to a file's `source_registry` / `evidence_sources`; object IDs are globally unique.
- Use line references where practical for local docs.
- User-provided facts are not automatically public claims. Mark them with the right status/scope and approval context.
- If a source cannot be committed safely, reference a sanitized source record rather than copying sensitive content into the repo.

## Schema, Scripts, and Exports

- Update schema/validator rules before adding many files that depend on a new shape.
- Keep validation deterministic and dependency-light where practical.
- Do not weaken validation to make bad data pass. Fix the data or document a deliberate extension path.
- SQLite exports are generated runtime artifacts. Do not commit generated databases unless an issue explicitly asks for that artifact.
- If validation tooling creates caches or build outputs, keep them ignored or remove them before commit.

## Branch Naming

| Prefix | Use |
|--------|-----|
| `docs/issue-N` | Docs/spec/conventions/examples |
| `schema/issue-N` | Schema or validator contract changes |
| `ontology/issue-N` | Client YAML/module/projection changes |
| `scripts/issue-N` | Validation/export/generator tooling |
| `fix/issue-N` | Bug fix in existing ontology/tooling |
| `chore/` | Repo maintenance with no ontology semantics change |

## Commit & PR Rules

- Commit per logical change with descriptive conventional commits.
- PRs must include:
  - linked issue;
  - summary of ontology/schema/tooling changes;
  - evidence/source notes for semantic changes;
  - validation commands run;
  - generated artifacts intentionally excluded or included;
  - acceptance criteria checklist.
- Self-review the diff before requesting review.
- Builder never merges their own PR.

## Verification Gates

Run the strongest relevant checks before reporting completion:

```bash
python3 scripts/validate_ontology.py
python3 scripts/export_sqlite.py --output build/client-ontologies.sqlite
```

Then verify:

- `git diff --check` has no whitespace errors.
- YAML parses and validator passes.
- Cross-file references resolve.
- Active/approved facts and rules have evidence.
- No obvious secret or sensitive-field patterns were introduced.
- Generated SQLite export succeeds when schema/runtime paths changed.
- `git status --short` shows only intended files.

**Post-push:** verify the remote commit is visible before reporting:

```bash
gh pr view <N> --json commits,headRefOid
```

**Post-merge:** verify GitHub reports the PR is actually merged before reporting success:

```bash
gh api repos/<owner>/<repo>/pulls/<N> --jq '{state, merged, merged_at, merge_commit_sha}'
```

## Review Checklist

Reviewers should block on:

- evidence-free verified/approved claims;
- public/client-facing claims without approval scope;
- ID churn or temporary tracker state in IDs;
- agent-specific canonical fields that should be projections/skills instead;
- duplicated concepts that should be relationships, interfaces, shared properties, or projections;
- schema/validator drift;
- generated artifacts committed unintentionally;
- secrets, tokens, raw private exports, payment data, or unnecessary PII;
- handoff output that leaks internal paths, private notes, or agent-only instructions.

## Human Approval Boundaries

Agents may draft, validate, reconcile, and recommend from ontology content.

Agents must not, without explicit human approval:

- publish or send client-facing handoff packages;
- mutate live client accounts, CMS records, GBP/listings, DNS, hosting, email, CRM, or payment systems;
- mark inferred/draft facts as approved public truth;
- broaden agent/tool authority based only on ontology rules;
- import raw private exports into the repo.

## Communication

| Channel | Use |
|---------|-----|
| External chat | Human ↔ Orchestrator decisions and approvals |
| GitHub Issues | Task tracking and acceptance criteria |
| GitHub PRs | Review, cross-review, and merge history |
| Repo files | Canonical ontology truth, docs, schemas, scripts |
| Downstream repos | Projections/consumers only unless explicitly promoted back here |

---

*Keep this file slim. Put detailed ontology semantics in `docs/spec.md`, conventions in `docs/conventions.md`, and implementation examples in `docs/examples.md`.*
