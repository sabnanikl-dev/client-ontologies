# Client Ontologies

Agent-agnostic operating ontologies for client workstreams, content models, approval rules, repo projections, and handoff documentation.

This repo is the canonical v0.1 YAML source of truth for client ontology facts and rules. Runtime stores such as SQLite, Postgres, Sanity, graph databases, or implementation repos are consumers/projections, not the authoring surface.

## Current status

Implemented v0.1 foundation:

- `schemas/` — JSON Schema contract, split by resource kind (`client`, `manifest`, `module`, `projection`) over shared `$defs` (`defs`, `evidence`, `rule`); `ontology.schema.json` is the umbrella dispatcher.
- `scripts/ontology_loader.py` — the single canonical YAML-reading entry point: `parse_yaml(path)`, manifest-first `iter_yaml(root)`, and `load_documents(root)`. Both the validator and exporter import it, so they parse files the same way and enumerate the same file set. Dependency-free (Ruby stdlib YAML).
- `scripts/validate_ontology.py` — deterministic validator: enforces the per-kind JSON Schema first, then canonical cross-file reference and evidence rules.
- `scripts/check_rules.py` — deterministic guardrail engine (stdlib-only, importable + CLI): runs a client's `machine_check` rules against draft copy and reports violations as JSON, with enforceable-severity exit codes.
- `scripts/ontology_service.py` + `scripts/ontology_cli.py` — the read-only **runtime consumer surface**: one transport-agnostic service (`list_clients`, `get_client_context`, `list_rules`, `check_copy`, `get_projection`, each returning a plain JSON dict with a `_meta` provenance stamp) behind the stdlib `ontology` CLI. Two interchangeable backends — canonical YAML (`--source yaml`) or a prebuilt SQLite export (`--source sqlite --sqlite-path …`, pure `sqlite3`, no Ruby) — return equivalent results. Read-only: no create/modify/delete; `check_copy` inherits `check_rules.py`'s exit codes. Fails closed (structured non-zero) on unknown client/projection, unavailable/foreign SQLite, backend drift, or bad args.
- `pyproject.toml` — packages the core with `ontology` (CLI) and `ontology-mcp` console entry points and **zero runtime dependencies**. The MCP stdio adapter itself is the next PR; the `ontology-mcp` entry point currently fails closed with a structured notice.
- `tests/run_cli.py` — proves the runtime surface: CLI acceptance (issue #19 cases), YAML/SQLite backend parity, projection isolation (negative leakage), planning-only preservation, and competency-corpus reuse.
- `scripts/check_evidence.py` — deterministic evidence-health check (stdlib-only, importable + CLI): reports two separate levels — **sources** (existence of every path-bearing registry source, even uncited: `present` / `missing` / `unavailable_in_environment`) and **citations** (re-hashing `content_hash` anchors, human or JSON). Verification is portable vs environment-local (`scope: portable` for repo-relative sources, `scope: environment_local` for an available external absolute path). `--strict` fails on genuine drift/missing/invalid/unsupported anchors or a missing repo-relative source; external absolute paths unavailable in the environment stay advisory (`unresolvable_in_environment` / source `unavailable_in_environment`), and an unknown `--client` is a usage error (exit 2).
- `tests/run_fixtures.py` — proves invalid fixtures (`tests/fixtures/`) are rejected for the expected reason.
- `tests/run_checks.py` — proves the guardrail engine's matching, advisory labeling, and exit-code semantics.
- `tests/run_evidence.py` — proves the evidence-health checker's utf8-lf-v1 hashing, drift/CRLF/invalid-range/unavailable-path categories, and strict exit semantics.
- `tests/competency/questions.yaml` + `tests/run_competency.py` — outcome-oriented competency suite: a test-owned registry of the business/governance questions each client ontology must answer, checked deterministically against a throwaway SQLite export (projection-scoped, with a drift-isolation regression).
- `scripts/export_sqlite.py` — optional runtime export into SQLite after YAML validates.
- `docs/spec.md` — Client Operating Ontology Spec v0.1.
- `docs/conventions.md` — ID, status, confidence, source, module, and approval conventions.
- `docs/examples.md` — concrete lookup examples for agents and scripts.
- `clients/femme-events/` — Femme Events manifest (`ontology.yaml`), client, modules, and projections.
- `clients/jmd-menswear/` — JMD Menswear manifest (`ontology.yaml`), client, modules, and projections.

Each client folder has an `ontology.yaml` **manifest** — the stable entry point that lists the client's modules and projections (with their IDs) and is what agents/scripts should load first.

## Design principles

- Canonical ontology source is version-controlled text, not an agent's private memory.
- Ontologies are agent-agnostic and tool-portable.
- Client-specific facts must cite a verified source path, URL, issue, or approval record.
- Public/client-facing handoff exports must not leak internal execution notes, credentials, or private context.
- Runtime stores such as SQLite, Postgres, Sanity, or graph databases are projections/consumers, not canonical in v0.
- Draft/inferred facts may help planning, but they must not be treated as approved public truth.

## Repository map

```text
client-ontologies/
  docs/
    spec.md
    conventions.md
    examples.md
    roadmap.md             # live open-issue sequencing (planning, not canonical)
    research/
      initial-ontology-design.md   # relocated design history + source inventory
  schemas/
    ontology.schema.json
    defs.schema.json
    evidence.schema.json
    rule.schema.json
    client.schema.json
    manifest.schema.json
    module.schema.json
    projection.schema.json
  scripts/
    ontology_loader.py     # shared YAML parse + manifest-first enumeration
    validate_ontology.py
    check_rules.py         # machine_check guardrail engine (library + CLI)
    check_evidence.py      # evidence-health / content_hash checker (library + CLI)
    export_sqlite.py
  tests/
    run_fixtures.py       # invalid fixtures must fail validation
    run_export.py          # valid fixture must validate + export
    run_checks.py          # guardrail engine matching + exit semantics
    run_evidence.py        # evidence-health hashing + strict exit semantics
    run_competency.py      # competency questions answered against a temp export
    competency/
      questions.yaml       # test-owned competency-question registry (not canonical)
    fixtures/
  .github/
    workflows/
      validate.yml         # CI: validate, export, and run tests on push/PR
  clients/
    femme-events/
      ontology.yaml        # manifest: entry point listing modules + projections
      client.yaml
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
      ontology.yaml        # manifest: entry point listing modules + projections
      client.yaml
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

## Ontology build order

Use this order for future client ontology work:

1. Read `docs/spec.md` and `docs/conventions.md`.
2. Gather trusted source material first: wiki notes, repo docs, local project docs, public URLs, readonly API snapshots, approval records.
3. Add or update schema/validator rules before adding many modules.
4. Add `clients/<client>/client.yaml` with source registry and privacy posture.
5. Add small workstream modules under `clients/<client>/modules/`.
6. Mark every fact as `verified`, `owner_reviewed`, `inferred`, `draft`, or `unknown`.
7. Add evidence for every active/approved public-facing fact, rule, and relationship.
8. Add projections only after canonical modules validate.
9. Add or update the client's `ontology.yaml` manifest so every module and projection file is registered with its ID.
10. Export runtime SQLite only after canonical YAML validation passes.

## Validate

Run from the repo root:

```bash
python3 scripts/validate_ontology.py
```

The validator checks:

- YAML parses through Ruby stdlib YAML.
- Each file validates against the JSON Schema for its `kind` (types, controlled-vocabulary enums, required identity fields, and no unknown fields — extensions must be `x_`-prefixed). Schema enforcement runs before the cross-reference checks.
- Required fields exist for clients, modules, and projections.
- IDs are stable, lowercase, and namespaced.
- Duplicate ontology object IDs are rejected.
- Every client directory has an `ontology.yaml` manifest, and each manifest resolves: every listed module/projection path exists, declared IDs match the loaded file IDs, each entry references the expected kind (`modules` → `ontology_module`, `projections` → `projection`) and the manifest's own `client_id`, no path escapes the client directory, and no module/projection file is left unregistered.
- Module references, entity references, rule references, and projection references resolve where practical.
- Verified/active/approved facts and rules have evidence.
- Evidence `source_id` references point to local source registries.
- Obvious secret patterns and sensitive field names are rejected.

## Tests

A set of dependency-light runners (no test framework) guard the validate → export path. Run them from the repo root:

```bash
python3 tests/run_fixtures.py    # every invalid fixture must FAIL validation
python3 tests/run_predicates.py  # predicate enum/constraint/inverse stay in sync
python3 tests/run_export.py      # the valid fixture must PASS, then export cleanly
python3 tests/run_competency.py  # each client still answers its competency questions
python3 tests/run_checks.py      # the guardrail engine matches + exits correctly
python3 tests/run_evidence.py    # the evidence-health checker hashes + exits correctly
```

- `run_fixtures.py` drives `tests/fixtures/<case>/` through the validator. Each case is a minimal repo root that must fail for one specific reason — covering schema-shape rejections (missing required fields, malformed IDs, bad enums, unknown kinds, unknown fields, malformed manifests/projections, and a malformed `machine_check` payload) and cross-reference/evidence/secret rejections (missing evidence on an active object, a dangling relationship endpoint, a projection referencing an unknown module, a duplicate ID, an uncompilable `regex_policy` pattern, and a committed secret pattern). It fails if any fixture unexpectedly passes or fails with the wrong message.
- `run_predicates.py` proves the relationship-predicate vocabulary stays in sync: the schema `predicate` enum, the validator's domain/range `PREDICATE_CONSTRAINTS`, and every `inverse` reference resolve to one another.
- `run_export.py` validates `tests/fixtures/valid/` (a complete, passing client), then runs `export_sqlite.py` into a temporary database and asserts the expected tables and row counts. The repo's own `build/` directory is never touched.
- `run_checks.py` exercises `scripts/check_rules.py` — the real-client acceptance cases (a blocking violation exits non-zero, a warning is reported but exits 0 until `--fail-on warning`) plus unit coverage of term/regex matching and the advisory (`draft`/`proposed` never gate) exit matrix.
- `run_competency.py` exercises the competency suite — a test-owned registry (`tests/competency/questions.yaml`) of the business/governance questions each client ontology must answer. Loading is **projection/client-directed**: for each question it builds a throwaway SCOPED SQLite export via the shared loader/export path (never the repo's `build/`) from only the named client's manifest, `client.yaml`, the named projection, and the modules that projection references — it never parses another client's files and never parses a module the projection excludes (`export(..., paths=...)` rejects any out-of-root path before parsing). On top of that scoped export it further scopes each query's **results** through the named projection and compares the normalized answer to the expected answer in the registry. It emits a human report and `--json`, names any failed question with expected-vs-actual diagnostics, exits non-zero on a failed required question, and runs four regressions: a loading-isolation regression and a synthetic resolver-read isolation regression — both instrumenting the **actual `parse_yaml` calls** — proving no question's scoped load reaches another client or opens an excluded module; a drift-isolation regression proving a single controlled semantic change fails **only** the relevant question; and a registry shape-validation regression proving a malformed question (non-string `id`/`client_id`/`projection`, non-boolean `required`, unknown select column, misspelled guard operand, a guard not bound to a selected output column, a non-scalar filter operand, wrong-typed expect payload) is rejected as a usage error (exit 2) before any answer is trusted. Competency questions are test requirements — not evidence, canonical truth, or authority — and no model, network, API credential, or live client system is required.
- `run_cli.py` exercises the runtime consumer surface (`scripts/ontology_service.py` + `scripts/ontology_cli.py`): the issue #19 CLI acceptance cases (a JMD blocking copy check exits non-zero; a Femme warning exits 0 until `--fail-on warning`; `context --projection femme-events.local-seo` resolves the projection's entities + rules), structured non-zero errors for unknown client/projection, unavailable SQLite, backend drift and malformed args, YAML⇄SQLite backend parity for every operation, projection isolation (a negative leakage case proving an excluded module never appears), planning-only preservation of Femme's draft/`baseline: unknown` metrics, and competency-corpus reuse — every question answered through the service equals `run_competency.py`'s own computed answer in both backends, reusing `evaluate_suite`/`load_questions` so no expected value is re-encoded.
- `run_evidence.py` exercises `scripts/check_evidence.py` — the pure utf8-lf-v1 helpers; per-citation classification against real temp files (verified match, drift from an in-span edit and from a line inserted before the range, CRLF/LF equivalence, invalid range, missing repo source, unavailable external path, unsupported hash version, and missing anchor); source-level existence coverage proving an *uncited* path-bearing source is still reported (present / missing repo-relative / unavailable external, with missing gating and external advisory); the portable-vs-environment-local scope distinction and the unknown-`--client` exit-2 usage error; and an end-to-end `--strict` pass proving drift fails while an unavailable external path stays advisory.

These fixtures live under `tests/` and use a synthetic `demo` client, so they are not picked up by a repo-root `validate_ontology.py` run.

## Continuous integration

`.github/workflows/validate.yml` runs on every push and pull request. It installs Python 3 and Ruby (the validator and exporter shell out to `ruby -e` for YAML parsing), then runs, in order: ontology validation, the SQLite export, `tests/run_fixtures.py`, `tests/run_predicates.py`, `tests/run_export.py`, `tests/run_competency.py`, `tests/run_checks.py`, `tests/run_cli.py`, and `tests/run_evidence.py`. Any validation, export, or check failure fails the build, so malformed data cannot merge — including a competency regression where a schema-valid change silently breaks a consumer answer. A final advisory `check_evidence.py --strict` step re-hashes cited spans and checks source existence: a repo-relative drift or a missing repo-relative source blocks, but external absolute paths unavailable on the runner report `unresolvable_in_environment` / source `unavailable_in_environment` and never fail the build.

## Export SQLite runtime projection

YAML remains canonical. Use SQLite only as a local runtime projection for agents/scripts:

```bash
python3 scripts/export_sqlite.py --output build/client-ontologies.sqlite
```

The exporter enumerates files through the same shared loader as the validator
(manifests first), so it exports the exact file set the validator gates on. The
export creates tables for:

- `manifests`
- `clients`
- `modules`
- `entities`
- `relationships`
- `rules`
- `projections`
- `sources`
- `evidence`

Example query:

```bash
sqlite3 build/client-ontologies.sqlite \
  "SELECT rule_id, severity, statement FROM rules WHERE client_id='jmd-menswear' AND status='active';"
```

## Run guardrail checks on draft copy

Before public copy changes, run a client's machine-checkable rules against the
draft with `scripts/check_rules.py` (stdlib-only; importable as a library and
usable as a CLI):

```bash
python3 scripts/check_rules.py --client jmd-menswear --text "Add to cart today"
```

It prints violations as JSON `[{rule_id, severity, status, matched, statement, advisory}]`.
Text comes from exactly one source — `--text`, `--file <path>`, or stdin — and
scope can be narrowed with `--workstream <name>` or `--projection <id>`. The exit
code is non-zero **only** when a violated rule is enforceable (`status` in
`active`/`approved`/`prohibited`) and its `severity` meets `--fail-on` (default
`blocking`; pass `--fail-on warning` to gate on warnings too). `draft`/`proposed`
rules are advisory and never change the exit code. See `docs/examples.md`,
Example 6, for the full walkthrough.

## Runtime consumer surface (read-only CLI)

The runtime surface lets a consumer load client context and enforce guardrails at
the point of action — one shared, transport-agnostic core with thin adapters, so
the transport choice is additive:

```text
scripts/ontology_loader.py   -- load + resolve projections (stdlib, #21)
scripts/check_rules.py       -- machine_check engine (stdlib, #11)
scripts/ontology_service.py  -- transport-agnostic ops, plain JSON dicts (#19)
        |
        +-- scripts/ontology_cli.py   <- v1 NOW  (stdlib CLI; CI / git-hook friendly)
        +-- server/ontology_mcp.py    <- NEXT     (thin MCP stdio adapter, isolated)
        +-- server/ontology_api.py    <- LATER    (thin HTTP adapter, additive)
```

**Placement & distribution.** The CLI, the (future) MCP server, and the shared
core all live in this repo, co-located with the schema and data they interpret,
so consumers register/install this implementation rather than reimplementing
parse + guardrail logic downstream (that would fork canonical semantics). An
agentic-harness consumer pins it by tag/SHA; `pyproject.toml` exposes `ontology`
(CLI) and `ontology-mcp` console entry points with zero runtime dependencies. The
CLI is the deterministic CI / pre-publish enforcement surface; MCP (next PR) is
the agent's query surface; consumers that need a Ruby-free path read the SQLite
projection.

Five read-only operations (no create/modify/delete — modeling an operation never
grants authority to run it):

```bash
ontology list-clients
ontology context     --client femme-events [--projection femme-events.local-seo]
ontology rules       --client femme-events [--severity blocking] [--workstream website]
ontology check-copy  --client femme-events --file draft.md [--fail-on warning]
ontology projection  --id femme-events.local-seo
```

Run the scripts directly without installing (equivalent to the console entry point):

```bash
python3 scripts/ontology_cli.py check-copy --client jmd-menswear --text "Add to cart today"
# -> flags jmd-menswear.website.showroom-not-ecommerce (blocking) and exits non-zero
```

Every response carries a `_meta` stamp (`read_mode`, `repo_commit`,
`generated_at`). Choose the backend with `--source yaml` (canonical YAML, uses
Ruby via the shared loader — the default) or `--source sqlite --sqlite-path
build/client-ontologies.sqlite` (a prebuilt export, pure `sqlite3`, **no Ruby**);
both return equivalent results. The surface **fails closed** with a structured
`{"error": …}` and a non-zero exit on an unknown client/projection, an unavailable
or foreign SQLite file, backend drift, or malformed args, and never returns a
resource outside the selected projection. `draft`/`inferred` resources are flagged
`planning_only` and are never presented as recorded outcomes.

### Installed-consumer snapshot contract

When the `ontology` command is **installed** into a consumer repo (pinned by
tag/SHA), it runs with that consumer's working directory as cwd — so it must be
pointed at an ontology snapshot explicitly. It does **not** discover the ontology
from the ambient `--root .`; a consumer repo has no `clients/` directory, and the
CLI **fails closed** there (structured `{"error": …}`, exit 2) rather than
returning an empty, vacuously "clean" result. The supported consumer pattern is:

- **Pin a SQLite snapshot.** The `client-ontologies` CI publishes the exported DB
  as a versioned artifact; a consumer's SessionStart hook fetches that snapshot to
  a known path (here `$ONTOLOGY_DB`). The `sqlite` backend is pure stdlib
  `sqlite3` — **no Ruby needed** at the consumer — and carries `repo_commit: null`
  (it never borrows the consumer repo's Git state as ontology provenance).
- **Always pass `--source sqlite --sqlite-path "$ONTOLOGY_DB"`** (or, if the
  consumer checks out this repo, an explicit `--source yaml --root <checkout>`).

`check-copy` is the enforcement surface — as a **pre-publish git hook** in a
consumer repo it gates a blocking violation before anything ships:

```bash
# .git/hooks/pre-commit (consumer repo)
#!/usr/bin/env bash
set -euo pipefail
# Pinned ontology snapshot fetched at session start (versioned; Ruby-free).
ONTOLOGY_DB="${ONTOLOGY_DB:-.ontology/client-ontologies.sqlite}"
for f in $(git diff --cached --name-only --diff-filter=ACM | grep -E '\.(md|html|mdx)$' || true); do
  ontology check-copy --client jmd-menswear \
      --source sqlite --sqlite-path "$ONTOLOGY_DB" --file "$f" || {
    echo "Blocked: '$f' violates a JMD ontology guardrail." >&2
    exit 1
  }
done
```

See `docs/examples.md` (Example 8) for the full CLI walkthrough.

## Check evidence health

Citations may carry portable anchors — `snapshot_date` and a versioned
`content_hash` (`sha256:utf8-lf-v1:<64 hex>`) — so drift in a cited source can be
detected. `scripts/check_evidence.py` (stdlib-only; importable + CLI) re-hashes
each anchored span and reports one category per citation:

```bash
python3 scripts/check_evidence.py --client femme-events            # human report
python3 scripts/check_evidence.py --json --strict                  # deterministic JSON, gating
```

The report has two levels. **Sources** (one row per path-bearing registry source,
even if uncited): `present`, `missing`, `unavailable_in_environment`. **Citations**
(one row per evidence ref): `verified_match`, `content_drift`, `source_missing`,
`anchor_missing`, `invalid_range`, `unsupported_hash_version`,
`unresolvable_in_environment`. Verification scope is explicit — repo-relative
sources verify `portable`; an available external absolute path verifies
`environment_local` only (a real check here, never a portable/CI guarantee). Without
`--strict` it is a pure report (exit 0). With `--strict` it exits 1 only on a genuine
failure (citation drift/source_missing/invalid/unsupported or source `missing`);
external absolute paths that are unavailable in the current environment stay advisory
and never gate — so the same command is safe to run in CI. An unknown `--client` is a
usage error (exit 2). See `docs/conventions.md` for the anchor-vs-vendor policy and
the re-confirmation workflow.

## How agents should consume projections

Load the client manifest first, then a projection — never scan the whole client folder:

1. Load `clients/<client>/ontology.yaml` to discover the available modules and projections (and their IDs).
2. Pick the projection that fits the task and load it.
3. Read only the listed modules/entities/rules unless the task requires deeper context.

Projection entry points:

- General context: `clients/<client>/projections/agent-context.yaml`
- Website work: `clients/<client>/projections/website-build.yaml`
- Femme local SEO work: `clients/femme-events/projections/local-seo.yaml`
- JMD inventory automation work: `clients/jmd-menswear/projections/inventory-workflow.yaml`

Projection consumption rules:

1. Load the projection.
2. Read only the listed modules/entities/rules unless the task requires deeper context.
3. Treat `draft` and `inferred` items as planning context, not public truth.
4. Do not mutate public accounts, send client-facing messages, deploy, merge, or publish just because a projection mentions a workflow. Approval rules still govern actions.
5. If a projection is missing a needed fact, update the canonical module first, run validation, then update the projection.

## Safety constraints

Do not encode:

- credentials, secrets, private keys, OAuth tokens, raw exports, or payment details;
- private residential address data;
- temporary issue/PR/sprint status as durable facts;
- unverified assumptions as `verified`;
- Hermes-specific behavior in canonical modules unless the file is explicitly a Hermes projection.

Do encode:

- evidence-backed business facts;
- approval-aware rules;
- draft/inferred status where the source is not approved;
- projection-specific slices for future agents/apps/workflows.
