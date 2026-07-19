# CLAUDE.md

Orientation for Claude Code sessions in this repo. Claude Code auto-loads this file.

`AGENTS.md` is the process bible (roles, core rules, evidence policy, approval gates, PR flow) — read it first and follow it. This file only covers the mechanical facts AGENTS.md leaves out: the file model, the commands, the gates, and the hidden dependency. It is not a second rulebook.

## File model: four resource kinds

Every canonical YAML file declares a `kind`. There are exactly four:

- `client` — `clients/<slug>/client.yaml`: client identity, `source_registry`, privacy posture, workstreams.
- `ontology` — `clients/<slug>/ontology.yaml`: the **manifest**. Lists the client's `modules` and `projections` (each with `path` + `id`). It is the entry point — load it first; it pins which module/projection files belong to the client.
- `ontology_module` — `clients/<slug>/modules/*.yaml`: canonical facts as `entities`, `relationships`, and `rules`, plus `evidence_sources`.
- `projection` — `clients/<slug>/projections/*.yaml`: curated consumer views that `include` module/entity/rule IDs. Runtime views, not new truth.

Manifest-first: `validate_ontology.py` loads `ontology.yaml` manifests before other files, every client directory must contain one (`kind: ontology`), and no module/projection file may be left unregistered by its manifest.

## Commands

Run from the repo root. All of these must pass before you commit:

```bash
python3 scripts/validate_ontology.py                                  # canonical gate
python3 scripts/export_sqlite.py --output build/client-ontologies.sqlite   # runtime projection
python3 tests/run_fixtures.py    # every invalid fixture must FAIL for its reason
python3 tests/run_export.py      # the valid fixture must validate + export cleanly
python3 tests/run_checks.py      # the machine_check guardrail engine matches + exits correctly
python3 tests/run_evidence.py    # the evidence-health checker hashes + exits correctly
```

`scripts/check_rules.py` is the runtime guardrail engine (library + CLI, stdlib
only): it runs a client's `machine_check` rules against copy
(`--client <slug> [--workstream <name> | --projection <id>] --text|--file|stdin`)
and exits non-zero only for enforceable (`active`/`approved`/`prohibited`) rules
meeting `--fail-on` (default `blocking`). It is not part of the core commit gate
above, but `tests/run_checks.py` guards it and runs in CI.

`scripts/check_evidence.py` is the runtime evidence-health engine (library + CLI,
stdlib only): it re-hashes cited spans against their `content_hash` anchors
(`sha256:utf8-lf-v1:<64 hex>`) and reports one category per citation
(`verified_match`, `content_drift`, `source_missing`, `anchor_missing`,
`invalid_range`, `unsupported_hash_version`, `unresolvable_in_environment`).
`--strict` exits non-zero only on genuine drift/missing/invalid/unsupported anchors;
external absolute paths unavailable in the environment stay advisory. It runs as an
advisory CI step and `tests/run_evidence.py` guards it.

- **Ruby must be on PATH.** The Python scripts parse YAML through the shared loader, which shells out to `ruby -e` (`require 'yaml'`), so the repo needs neither PyYAML nor jsonschema — but a session without `ruby` will fail every parse. There are no pip/gem dependencies otherwise.
- **`build/` is gitignored** (along with `__pycache__/`, `*.py[cod]`, `.DS_Store`). The SQLite export is a local runtime artifact — never commit it unless an issue explicitly asks.

## Where the gates live: schema layer vs. validator cross-reference pass

`validate_ontology.py` runs two enforcement layers, schema first, then repo-specific checks. Know which layer owns which failure before you try to "fix" a rejection.

**Schema layer** (`schemas/*.schema.json`, checked by a built-in draft-2020-12-subset evaluator — no external jsonschema). Four per-kind schema files are dispatched by `kind` via the validator's `KIND_SCHEMA` map — `client` → `client.schema.json`, `ontology` → `manifest.schema.json`, `ontology_module` → `module.schema.json`, `projection` → `projection.schema.json` — over shared `$defs` (`defs`, `evidence`, `rule`); `ontology.schema.json` is the umbrella. It enforces **shape**:

- types, `const`/`enum` controlled vocabularies, string `pattern`s, `required` identity fields, `minItems`/`minProperties`;
- **unknown fields are rejected on structured schema objects** (`additionalProperties: false`), where the only escape hatch is an `x_`-prefixed key (`patternProperties: ^x_`) for local extensions. A few containers are intentionally left open and accept arbitrary inner keys: `entity.fields` (bare `{"type": "object"}`, so `additionalProperties` defaults to allowed) and per-state entries inside a state machine's `states` (`additionalProperties: true`). `rule.machine_check` is **not** open: it is type-discriminated by a `oneOf` keyed on `type` accepting exactly `disallowed_terms`, `required_terms`, and `regex_policy`, each with a fixed `additionalProperties: false` payload (still allowing `x_`-prefixed extensions); unknown types and malformed payloads fail. `scripts/check_rules.py` executes these checks and its type set is kept in lockstep with this `oneOf`.

**Cross-reference / semantic pass** (Python in `validate_ontology.py`, runs after schema passes). It enforces **meaning**:

- ID hygiene: lowercase/namespaced pattern, IDs namespaced under their `client_id`, and global uniqueness of ontology object IDs (evidence `source_id`s are file-local, not global);
- evidence conditions: entities and rules in a public/enforced status (`active`, `approved`, `prohibited`, `owner_reviewed_internal`), and any entity, relationship, or rule with `source_confidence: verified`, must carry `evidence`; non-manifest, non-client files need a non-empty `evidence_sources`; every evidence `source_id` must resolve to a local source registry;
- reference resolution: relationship `subject`/`object` → known entities; projection `includes` → known modules/entities/rules (supports `.*` wildcards);
- executable-payload sanity: every rule's `regex_policy` `machine_check` pattern is `re.compile`d here (the schema only checks it is a string), so an uncompilable pattern fails validation rather than crashing `scripts/check_rules.py` at runtime;
- manifest membership: each declared `path` exists and stays inside the client dir, declared `id`/`kind`/`client_id` match the target file, and no file is unregistered;
- secret/sensitive-field scanning: known secret token patterns (`SECRET_PATTERNS`) and a fixed, narrow set of sensitive-looking field names (`password`, `api_key`, `access_token`, `refresh_token`, `private_key`, `client_secret`) are rejected — this does not scan PII values or categories generally.

If schema and validator ever disagree about a shape, update the schema first (AGENTS.md: update schema/validator before adding files that depend on a new shape); don't weaken a check to make bad data pass.
