# Client Ontologies

Agent-agnostic operating ontologies for client workstreams, content models, approval rules, repo projections, and handoff documentation.

This repo is the canonical v0.1 YAML source of truth for client ontology facts and rules. Runtime stores such as SQLite, Postgres, Sanity, graph databases, or implementation repos are consumers/projections, not the authoring surface.

## Current status

Implemented v0.1 foundation:

- `schemas/` — JSON Schema contract, split by resource kind (`client`, `manifest`, `module`, `projection`) over shared `$defs` (`defs`, `evidence`, `rule`); `ontology.schema.json` is the umbrella dispatcher.
- `scripts/ontology_loader.py` — the single canonical YAML-reading entry point: `parse_yaml(path)`, manifest-first `iter_yaml(root)`, and `load_documents(root)`. Both the validator and exporter import it, so they parse files the same way and enumerate the same file set. Dependency-free (Ruby stdlib YAML).
- `scripts/validate_ontology.py` — deterministic validator: enforces the per-kind JSON Schema first, then canonical cross-file reference and evidence rules.
- `scripts/check_rules.py` — deterministic guardrail engine (stdlib-only, importable + CLI): runs a client's `machine_check` rules against draft copy and reports violations as JSON, with enforceable-severity exit codes.
- `tests/run_fixtures.py` — proves invalid fixtures (`tests/fixtures/`) are rejected for the expected reason.
- `tests/run_checks.py` — proves the guardrail engine's matching, advisory labeling, and exit-code semantics.
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
    export_sqlite.py
  tests/
    run_fixtures.py       # invalid fixtures must fail validation
    run_export.py          # valid fixture must validate + export
    run_checks.py          # guardrail engine matching + exit semantics
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

Two dependency-light runners (no test framework) guard the validate → export path. Run both from the repo root:

```bash
python3 tests/run_fixtures.py   # every invalid fixture must FAIL validation
python3 tests/run_export.py     # the valid fixture must PASS, then export cleanly
python3 tests/run_checks.py     # the guardrail engine matches + exits correctly
```

- `run_fixtures.py` drives `tests/fixtures/<case>/` through the validator. Each case is a minimal repo root that must fail for one specific reason — covering schema-shape rejections (missing required fields, malformed IDs, bad enums, unknown kinds, unknown fields, malformed manifests/projections, and a malformed `machine_check` payload) and cross-reference/evidence/secret rejections (missing evidence on an active object, a dangling relationship endpoint, a projection referencing an unknown module, a duplicate ID, and a committed secret pattern). It fails if any fixture unexpectedly passes or fails with the wrong message.
- `run_export.py` validates `tests/fixtures/valid/` (a complete, passing client), then runs `export_sqlite.py` into a temporary database and asserts the expected tables and row counts. The repo's own `build/` directory is never touched.
- `run_checks.py` exercises `scripts/check_rules.py` — the real-client acceptance cases (a blocking violation exits non-zero, a warning is reported but exits 0 until `--fail-on warning`) plus unit coverage of term/regex matching and the advisory (`draft`/`proposed` never gate) exit matrix.

These fixtures live under `tests/` and use a synthetic `demo` client, so they are not picked up by a repo-root `validate_ontology.py` run.

## Continuous integration

`.github/workflows/validate.yml` runs on every push and pull request. It installs Python 3 and Ruby (the validator and exporter shell out to `ruby -e` for YAML parsing), then runs, in order: ontology validation, the SQLite export, `tests/run_fixtures.py`, `tests/run_export.py`, and `tests/run_checks.py`. Any validation, export, or check failure fails the build, so malformed data cannot merge.

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
