# Client Ontologies

Agent-agnostic operating ontologies for client workstreams, content models, approval rules, repo projections, and handoff documentation.

This repo is the canonical v0.1 YAML source of truth for client ontology facts and rules. Runtime stores such as SQLite, Postgres, Sanity, graph databases, or implementation repos are consumers/projections, not the authoring surface.

## Current status

Implemented v0.1 foundation:

- `schemas/ontology.schema.json` — generic schema contract for clients, modules, entities, relationships, rules, evidence sources, and projections.
- `scripts/validate_ontology.py` — deterministic validator for canonical YAML and cross-file references.
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
  scripts/
    validate_ontology.py
    export_sqlite.py
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
- Required fields exist for clients, modules, and projections.
- IDs are stable, lowercase, and namespaced.
- Duplicate ontology object IDs are rejected.
- Each client `ontology.yaml` manifest resolves: every listed module/projection path exists, declared IDs match the loaded file IDs, and no module/projection file is left unregistered.
- Module references, entity references, rule references, and projection references resolve where practical.
- Verified/active/approved facts and rules have evidence.
- Evidence `source_id` references point to local source registries.
- Obvious secret patterns and sensitive field names are rejected.

## Export SQLite runtime projection

YAML remains canonical. Use SQLite only as a local runtime projection for agents/scripts:

```bash
python3 scripts/export_sqlite.py --output build/client-ontologies.sqlite
```

The export creates tables for:

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
