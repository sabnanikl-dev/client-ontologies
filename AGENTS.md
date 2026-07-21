# Client Ontology Agent Harness

Slim process bible. All agents must read this before touching the repo.

This repo is the canonical, agent-agnostic source of truth for client operating ontologies. Treat it like infrastructure: small diffs, explicit evidence, deterministic validation, and human approval for authority-changing work.

## Roles

- Human: owns client truth, taste, approval boundaries, and public/client-facing or authority-changing approvals.
- Orchestrator: reads issues, reconstructs repo state, scopes work, coordinates builders/reviewers, and reports verified outcomes.
- Ontology Builder: edits YAML/docs/schemas/scripts for one issue at a time; does not self-approve.
- Ontology Reviewer: reviews schema safety, evidence quality, references, validation/export behavior, and handoff safety.
- Consumer/App Builder: uses projections/exports; does not redefine canonical ontology truth downstream.

## Core Rules

1. Canonical truth lives here. Repo-local ontology copies in client/app repos are projections unless explicitly promoted.
2. Agents are ontology stewards. When issue work changes ontology concepts, module boundaries, schema expectations, validation behavior, or consumer semantics, update relevant docs in the same PR: `docs/spec.md`, `docs/conventions.md`, and/or `docs/examples.md`.
3. Evidence beats memory. Durable client facts, rules, relationships, and public claims need source-backed evidence or must be marked `draft`, `inferred`, or `unknown`.
4. One issue, one branch, one PR. Do not bundle unrelated clients, modules, schema changes, and generated exports unless the issue requires it.
5. Generation is not evaluation. Builders do not review their own ontology changes.
6. Human gate is load-bearing. No publishing, account mutation, client-facing handoff, authority expansion, or approval-status promotion without explicit human approval.
7. No secrets or raw private exports. Never commit credentials, OAuth tokens, private keys, raw client exports, payment data, or unnecessary PII.
8. If it is not in the repo, cite it. External knowledge can inform work, but ontology claims must point to a repo file, issue, URL, approval record, or documented source.

## Session Start

Every session begins with state reconstruction:

```bash
git status --short --branch
# read: AGENTS.md, README.md, docs/spec.md, docs/conventions.md
# read assigned issue + acceptance criteria
# inspect relevant client/module/projection files
git log --oneline -20
python3 scripts/validate_ontology.py
```

Fresh session every time. Do not rely on prior chat context or `--continue`.

## Repository Map

Key files: `README.md`, `docs/spec.md`, `docs/conventions.md`, `docs/examples.md`, `schemas/`, `scripts/validate_ontology.py`, `scripts/export_sqlite.py`, and the read-only runtime consumer surface `scripts/ontology_service.py` + `scripts/ontology_cli.py` (packaged via `pyproject.toml`).

Client layout: `clients/<client-slug>/client.yaml`, `modules/*.yaml` for canonical ontology facts/rules/relationships/workflows, and `projections/*.yaml` for curated consumer views.

## Ontology Authoring

- Model real-world client concepts, not source-system tables or agent-specific convenience objects.
- Keep modules small and workstream-oriented: brand, website, local visibility, operations, inventory, handoff.
- Use stable, lowercase, namespaced IDs: `client-slug.workstream.object-name`; never encode issue/PR/sprint/current-task state in IDs.
- Separate canonical truth from projections: modules are canonical; projections are curated views; SQLite/build outputs are runtime only.
- Mark uncertainty honestly: `verified`, `owner_reviewed`, `inferred`, `draft`, or `unknown`.
- Prefer explicit relationships over hiding semantics inside prose.
- Put approval rules and authority boundaries in operations/governance modules, not brand voice or website copy.

## Evidence and Sources

- Every active/approved public-facing entity, relationship, rule, claim, approval gate, or handoff item needs evidence.
- Evidence `source_id`s are local to each file's registry; ontology object IDs are globally unique.
- Use line references where practical.
- User-provided facts are not automatically public claims; mark scope/status correctly.
- If a source cannot be committed safely, reference a sanitized source record instead of copying sensitive content.

## Schema, Exports, and Validation

- Update schema/validator rules before adding many files that depend on a new shape.
- Keep validation deterministic and dependency-light.
- Do not weaken validation to make bad data pass; fix the data or document an extension path.
- Do not commit generated SQLite/build artifacts unless the issue explicitly asks for them.
- Remove or ignore caches/build outputs created by tooling.

Strongest relevant checks before reporting completion:

```bash
python3 scripts/validate_ontology.py
python3 scripts/export_sqlite.py --output build/client-ontologies.sqlite
git diff --check
```

Also verify YAML parses, cross-file references resolve, active/approved facts have evidence, no secrets were introduced, and `git status --short` shows only intended files.

## Branches, Commits, and PRs

Branch prefixes: `docs/issue-N`, `schema/issue-N`, `ontology/issue-N`, `scripts/issue-N`, `fix/issue-N`, or `chore/`.

Commit per logical change. PRs must include linked issue, summary of ontology/schema/tooling changes, documentation/spec updates made or why none were needed, evidence/source notes, validation commands run, generated artifacts included/excluded, and acceptance checklist.

- Agent-facing docs (`CLAUDE.md`/`README.md`) updated when the file model, commands, or gates change.

Self-review the diff before requesting review. Builders never merge their own PRs.

Post-push: verify remote commit with `gh pr view <N> --json commits,headRefOid`. Post-merge: verify `merged: true` with `gh api repos/<owner>/<repo>/pulls/<N> --jq '{state, merged, merged_at, merge_commit_sha}'`.

## Review and Approval Gates

Reviewers should block on evidence-free verified/approved claims, public/client-facing claims without approval scope, ID churn, temporary tracker state in IDs, agent-specific canonical fields, duplicated concepts that should be relationships/interfaces/projections, schema/validator drift, unintended generated artifacts, secrets/PII, or handoff output that leaks internal paths/private notes/agent-only instructions.

Agents may draft, validate, reconcile, and recommend from ontology content. Without explicit human approval, agents must not publish or send client-facing packages, mutate live client accounts/CMS/GBP/DNS/hosting/email/CRM/payment systems, mark inferred/draft facts as approved public truth, broaden authority based only on ontology rules, or import raw private exports.

## Communication
Use external chat for human decisions, GitHub Issues for task tracking, PRs for review/merge history, repo files for canonical ontology truth/docs/schemas/scripts, and downstream repos for projections/consumers only unless explicitly promoted back here.
