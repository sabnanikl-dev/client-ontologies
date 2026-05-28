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

Every file is validated against the JSON Schema for its `kind` (`schemas/client.schema.json`, `module.schema.json`, or `projection.schema.json`) before the deterministic cross-reference checks run. The schemas are strict: structured objects use `additionalProperties: false`, so unknown fields fail validation. To add a field the schema does not yet model, namespace it with an `x_` prefix (e.g. `x_internal_note`); promote it to a real schema property once it stabilises. The free-form `entity.fields` bag and `rule.machine_check` body remain intentionally open.

## Canonical vs runtime

YAML in this repository is canonical. SQLite and any future database/export are runtime projections only.
