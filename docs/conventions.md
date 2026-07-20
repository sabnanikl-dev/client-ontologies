# Client Ontology Conventions v0.1

These conventions keep client ontology files diffable, evidence-backed, and easy for agents/apps/workflows to consume.

## ID naming

- Client IDs use lowercase kebab case: `femme-events`, `jmd-menswear`.
- Module IDs are namespaced by client and workstream: `femme-events.brand`, `jmd-menswear.inventory-images`.
- Entity IDs are stable, namespaced nouns: `femme-events.brand.voice`, `jmd-menswear.inventory.image`.
- Relationship IDs are stable triples in prose order: `femme-events.website.uses-brand-voice`.
- Rule IDs are namespaced by client/workstream: `jmd-menswear.website.no-ecommerce-language`.
- Projection IDs use `client.projection-name`: `femme-events.website-build`.
- Do not encode issue numbers, PR numbers, sprint names, or temporary tracker state in IDs.

## Status values

- `draft` — proposed or useful internally, not approved for public/live use.
- `proposed` — ready for review, but not yet authoritative.
- `active` — governs current work and is backed by evidence.
- `approved` — explicitly approved for its stated scope.
- `owner_reviewed_internal` — reviewed for planning/internal use but still may require mutation-specific approval.
- `deprecated` — retained for history; do not use for new work.
- `prohibited` — explicitly disallowed.
- `unknown` — a field is intentionally unresolved.

## Source confidence levels

- `verified` — source-backed and safe to use within its stated status/scope.
- `owner_reviewed` — reviewed by Karan/Amanda/client owner for internal or public scope stated in evidence.
- `inferred` — synthesized from trusted sources; needs review before public use.
- `draft` — proposed design or operating model.
- `unknown` — not known; do not normalize silently.

## Evidence source types

Allowed source types follow `docs/spec.md`:

- `obsidian_note`
- `local_project_doc`
- `git_repo_file`
- `github_issue`
- `github_pr`
- `linear_issue`
- `client_email_or_message`
- `public_url`
- `api_readonly_snapshot`
- `human_approval_record`
- `user_preference`

Each durable public-facing fact/rule should cite evidence with a `source_id` and, where practical, `lines`.

## Portable evidence anchors: `snapshot_date` and `content_hash`

`evidenceRef` accepts two optional anchor fields so a citation can be *verified*, not just pointed at. Both are optional — existing citations keep validating unchanged.

- `snapshot_date` — the ISO date (`YYYY-MM-DD`) the cited span was last confirmed against the source.
- `content_hash` — a **versioned** SHA-256 of the cited lines, in exactly this shape:

  ```text
  sha256:utf8-lf-v1:<64 lowercase hex characters>
  ```

  `utf8-lf-v1` means: decode the source as UTF-8; normalize CRLF and CR to LF; interpret `lines` as 1-based inclusive range(s) (the same `"a-b,c-d,e"` grammar used elsewhere); join the selected logical lines with `\n`; do **not** append a trailing newline; SHA-256 the resulting UTF-8 bytes. A line-ending-only change therefore does not create false drift. Any future change to this normalization must ship under a **new** version tag (`utf8-lf-v2`, …) rather than silently changing existing hashes; the schema constrains the value to `sha256:utf8-lf-v1` today.

`scripts/check_evidence.py` reports two separate levels. At the **source** level it reports the existence of every registry source that declares a `path` — even one no citation references — as `present`, `missing` (repo-relative absent), or `unavailable_in_environment` (external absent, advisory). At the **citation** level it re-computes each anchored span and reports one category per evidence ref — `verified_match`, `content_drift`, `source_missing`, `anchor_missing`, `invalid_range`, `unsupported_hash_version`, or `unresolvable_in_environment`. Verification is **portable vs environment-local**: a repo-relative anchor is verified portably (`scope: portable`), while an available external absolute path is verified environment-locally only (`scope: environment_local`) — a genuine check on that machine, never presented as a portable/CI guarantee. Under `--strict` it exits non-zero only on genuine failures (citation drift/source_missing/invalid/unsupported or source `missing`); an unknown `--client` is a usage error (exit 2). **External absolute paths that are unavailable in the current environment stay advisory** — a citation is never reported as verified against a source the environment cannot read, so the check is safe to run in CI without blocking on owner-only paths.

### Anchor vs. vendor: which to use for a load-bearing fact

For a load-bearing `verified`/`owner_reviewed_internal` fact whose source is a private local file, choose in this order:

1. **Anchor the external source (preferred default).** Keep the existing `path` (e.g. the owner's SOT) and add `snapshot_date` + `content_hash` computed from the cited lines. Nothing private is copied into the repo; CI is advisory on that path; and an owner running `check_evidence.py` locally (where the path resolves) gets real drift detection. Use this unless a sanitized excerpt is clearly safer and better.
2. **Vendor a sanitized excerpt.** Only when a small, sanitized quote is genuinely safer/clearer, commit it under `clients/<client>/sources/`, cite it as a repo-relative `git_repo_file`, and anchor it. This makes the anchor verifiable in CI. It is **optional** — no `verified` fact is required to commit private source text merely to satisfy the health check.

Never vendor raw private exports, credentials, unnecessary PII, or a full private-source duplication (AGENTS.md rule 7). A vendored excerpt must be a minimal, sanitized quote.

### Confirmation workflow (when re-anchoring)

1. Open the current source and re-read the cited span.
2. Recompute the anchor: `python3 scripts/check_evidence.py --strict` (locally, where the path resolves) — `verified_match` means the anchor is current; `content_drift` means the span moved or changed.
3. On drift, re-confirm the fact against the source, update `lines`/`content_hash`, and set `snapshot_date` to the confirmation date. If the underlying fact itself changed, update the ontology object (and its status/evidence) — do not just re-stamp the hash.

## Module boundaries

- `brand` — identity, voice, visual tokens, tone rules.
- `website` — website pages, routes, CMS/fallbacks, public copy guardrails.
- `local-visibility` — GBP, citations, NAP, service areas, reviews, public listing guardrails.
- `operations` — approval boundaries, workflow defaults, owner/operator constraints.
- `inventory-images` — JMD Drive/Sanity/image lifecycle and showroom workflow.

Keep modules small. If a module becomes hard to review, split by workstream rather than adding nested complexity.

## Approval boundaries

Agents and automations may draft, reconcile, validate, and recommend from ontology content. They must not publish, mutate public accounts, send client-facing messages, or change live sites/accounts unless a human approval record covers that exact action and scope.

## Schema enforcement and extensions

Every file is validated against the JSON Schema for its `kind` (`schemas/client.schema.json`, `manifest.schema.json` for the `ontology` manifest, `module.schema.json`, or `projection.schema.json`) before the deterministic cross-reference checks run. The schemas are strict: structured objects use `additionalProperties: false`, so unknown fields fail validation. To add a field the schema does not yet model, namespace it with an `x_` prefix (e.g. `x_internal_note`); promote it to a real schema property once it stabilises. The free-form `entity.fields` bag remains intentionally open. The `rule.machine_check` body is **type-discriminated** (a `oneOf` keyed on `type`): v1 accepts exactly `disallowed_terms`, `required_terms`, and `regex_policy`, each with a fixed payload shape, and each branch still permits `x_`-prefixed extensions. Unknown types and malformed payloads fail validation; `scripts/check_rules.py` executes these checks against copy. Because the schema can only assert that a `regex_policy` `pattern` is a string, the cross-reference pass also `re.compile`s each pattern, so an uncompilable regex fails validation instead of crashing the guardrail engine at runtime.

## Canonical vs runtime

YAML in this repository is canonical. SQLite and any future database/export are runtime projections only.
