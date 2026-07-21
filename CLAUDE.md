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
python3 tests/run_competency.py  # each client still answers its competency questions (outcome regression)
python3 tests/run_checks.py      # the machine_check guardrail engine matches + exits correctly
python3 tests/run_evidence.py    # the evidence-health checker hashes + exits correctly
python3 tests/run_cli.py         # the read-only runtime CLI/service: acceptance, YAML/SQLite parity, isolation
```

`tests/run_competency.py` is the outcome-oriented suite: it reads the test-owned
registry `tests/competency/questions.yaml` (NOT a canonical `kind`; never loaded
as client truth) and proves each client ontology still answers its
business, technical/data-flow, and bounded multi-hop competency questions — the
proof behind the client coverage contract in `docs/coverage.md`. Its
projection-scoped query vocabulary is `entities`/`rules`/`projection_resources`
plus (issue #41) `relationships` (subject/predicate/object rows, in scope only
when the edge's module is in scope AND both endpoints are in-scope entities) and
`path` (a **deliberately bounded, deterministic** simple-path traversal with
explicit `start`/`end` constraints, allowed `predicates`, and `min_hops`/
`max_hops` ≤ `PATH_HOP_CAP` — NOT a general graph query; no GraphRAG, embeddings,
model grading, cross-client loading, or second store). Guards keep answers safe:
status / `require_field_in` / `forbid_field_in` guards prove a draft plan is not
presented as verified current architecture, and `forbid_id_prefix` /
`require_edge_confidence_in` / `forbid_edge_confidence_in` guards (each bound to
the query's output shape) prove no other client's or excluded module's resource
leaks in. Loading is **projection/client-directed** (issue #31 AC): for each
question it builds a throwaway SCOPED export through the shared loader/export path
(never the repo's `build/`) from only the named client's manifest, `client.yaml`,
the named projection, and the modules that projection references — never another
client's files and never a module the projection excludes (`resolve_scope_paths`
computes the file set — widening to the full single-client module set rather than
scanning-and-excluding when a reference points outside `includes.modules`;
`export_sqlite.export(..., paths=...)` reuses the same `parse_yaml`/table shapes
and rejects any path outside `root`). Results are then further scoped through the
named projection (relationship endpoints and every traversed path node stay in
scope). Every controlled operand a question names — a relationship/path
`predicate`, an entity `entity_type`, a `source_confidence`, a `status`, a rule
`severity` — is validated against the SAME vocabulary the schema enforces (loaded
from `schemas/*.schema.json`, with the schema's bounded `x_` predicate escape);
every filter/expected operand is also type-checked per column (a bool/number
against a string column like `subject` is rejected, not silently unmatched); the
`expect` envelope is closed; and each expected path chain is checked against its
query's hop bounds / allowed predicates / endpoint constraints, rejects a
repeated-node (cyclic) chain the simple-path traversal could never return, and —
once the scoped export exists — rejects an expected endpoint whose actual
`entity_type` contradicts the query's `start`/`end` constraint. So a misspelled
predicate or an impossible chain is a usage error, not a silently-empty answer a
required question reports as PASS. A `required` (coverage-proof) question must
assert a **non-empty** expected answer — an empty required expectation with
vacuous guards can never count as coverage; a deliberately empty answer must be
`required: false` (a future explicit absence-query DSL stays out of scope). A
failed **optional** question never gates the exit code but is reported honestly
(the summary says "N required passed; K optional FAILED", never a blanket
"all passed"). Parallel edges collapse to one path (the public representation
carries no edge identity). Six regressions back it: **loading-isolation** and
**resolver-read isolation** (both instrument the ACTUAL `parse_yaml` calls — not
just returned path lists — to prove no scoped load reaches another client or opens
a module the projection excludes), **relationship/path scope-isolation** (a
synthetic full export proves, at the result boundary, that a relationship endpoint
in an excluded module is dropped and a bounded path never traverses an out-of-scope
node, a disallowed predicate, or beyond its hop bound), **path-shape** (a synthetic
fixture proves parallel edges dedupe to one path, a back-edge cannot revisit a node
on a simple path, branching yields distinct paths, ordering is deterministic, and
an expected endpoint's `entity_type` must match the query's constraint), **drift-
isolation** (a single controlled mutation — a status, a projection membership, a
relationship confidence, or a path-edge confidence — fails only the relevant
question), and **registry shape-validation** (a malformed question — including a
non-string `id`/`client_id`/`projection`, a non-boolean `required`, a required
question with an empty expected answer, a guard not bound to a selected output
column, a non-scalar or wrong-typed filter operand, a typo'd
`predicate`/`entity_type`/`source_confidence`/`status`/`severity` token, a
stray/unknown `expect` key, an expected chain that contradicts its query (a
disallowed predicate, a bad hop count, a mismatched endpoint, or a repeated node),
or a malformed `path` contract — is rejected as a usage error / exit 2 before any
answer is trusted).
Expected answers live only in the registry, so a consumer (issue #19) can reuse
the corpus via `evaluate_suite(db_path, questions)` to prove YAML/SQLite parity
without re-encoding them; the relationship/`path` ops are test-owned and their
runtime consumer surface is deferred to the separate query-surface issue. It
needs no model, network, API credential, or live client system.

`scripts/check_rules.py` is the runtime guardrail engine (library + CLI, stdlib
only): it runs a client's `machine_check` rules against copy
(`--client <slug> [--workstream <name> | --projection <id>] --text|--file|stdin`)
and exits non-zero only for enforceable (`active`/`approved`/`prohibited`) rules
meeting `--fail-on` (default `blocking`). It is not part of the core commit gate
above, but `tests/run_checks.py` guards it and runs in CI.

`scripts/ontology_service.py` + `scripts/ontology_cli.py` are the **read-only
runtime consumer surface** (issue #19). The service is transport-agnostic: five
pure-read operations returning plain JSON dicts — `list_clients`,
`get_client_context` (projection-scoped entities + active rules),
`list_rules`, `check_copy` (reuses #11's `evaluate_rule`/`compute_exit`, so its
exit code is inherited verbatim), and `get_projection` (resolved slice +
provenance). Every response carries a `_meta` stamp (`read_mode`, `repo_commit`,
`generated_at`). Two interchangeable backends sit behind one normalized model:
`--source yaml` (canonical YAML via the shared loader — uses Ruby) and
`--source sqlite --sqlite-path build/…​.sqlite` (a prebuilt export — **pure
stdlib `sqlite3`, never invokes Ruby**); both reconstruct the same per-resource
documents, so answers are equal by construction. It is **read-only** — no
create/modify/delete, no account/CMS/GBP mutation; modeling an operation grants
no authority to run it. It **fails closed** (structured non-zero error) on an
unknown client/projection, an unavailable/foreign SQLite file, backend drift, or
malformed args, and never returns a resource outside the selected projection.
`draft`/`inferred` resources are flagged `planning_only` and never presented as
recorded outcomes. The CLI (`ontology <cmd>`, console entry point in
`pyproject.toml`) is the enforcement surface: `ontology check-copy --client
<slug> --file draft.md` drops into a consumer's CI or pre-publish git hook and
exits non-zero on a blocking violation. `pyproject.toml` also registers an
`ontology-mcp` entry point; the MCP stdio adapter itself (`server/`) is the next
PR, so that entry point currently fails closed with a structured notice.
`tests/run_cli.py` guards all of this (and reuses #31's `evaluate_suite`/
`load_questions` corpus to prove YAML/SQLite answer parity without re-encoding any
expected value) and runs in CI.

`scripts/check_evidence.py` is the runtime evidence-health engine (library + CLI,
stdlib only). It reports at two separate levels: **sources** (one row per
path-bearing registry source, even if uncited — `present` / `missing` /
`unavailable_in_environment`) and **citations** (one row per evidence ref, re-hashing
`content_hash` anchors of the form `sha256:utf8-lf-v1:<64 hex>` — `verified_match`,
`content_drift`, `source_missing`, `anchor_missing`, `invalid_range`,
`unsupported_hash_version`, `unresolvable_in_environment`). Verification is
**portable vs environment-local**: a repo-relative anchor is verified portably
(`scope: portable`), while an available external absolute path is verified
environment-locally only (`scope: environment_local`) and never claimed as a
portable/CI guarantee. `--strict` exits 1 only on genuine failures (citation
drift/source_missing/invalid/unsupported or source `missing`); external absolute
paths unavailable in the environment stay advisory, and an unknown `--client` is a
usage error (exit 2). It runs as an advisory CI step and `tests/run_evidence.py`
guards it.

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
- predicate domain/range: for the bounded, high-confidence subset in `PREDICATE_CONSTRAINTS` (`measures`→subject `metric`, `governed_by`→object `governance_object`, `contains`→subject `system_resource`), the resolved endpoint `entity_type` must be allowed; a self-check also rejects any constraint key that has left the schema predicate enum (`schemas/module.schema.json` `$defs.predicateName`). The `predicate` enum itself, optional `cardinality`, and optional `inverse` (which must name a vocabulary predicate) are enforced in the schema layer; `tests/run_predicates.py` guards enum/constraint/`inverse` sync;
- executable-payload sanity: every rule's `regex_policy` `machine_check` pattern is `re.compile`d here (the schema only checks it is a string), so an uncompilable pattern fails validation rather than crashing `scripts/check_rules.py` at runtime;
- manifest membership: each declared `path` exists and stays inside the client dir, declared `id`/`kind`/`client_id` match the target file, and no file is unregistered;
- secret/sensitive-field scanning: known secret token patterns (`SECRET_PATTERNS`) and a fixed, narrow set of sensitive-looking field names (`password`, `api_key`, `access_token`, `refresh_token`, `private_key`, `client_secret`) are rejected — this does not scan PII values or categories generally.

If schema and validator ever disagree about a shape, update the schema first (AGENTS.md: update schema/validator before adding files that depend on a new shape); don't weaken a check to make bad data pass.
