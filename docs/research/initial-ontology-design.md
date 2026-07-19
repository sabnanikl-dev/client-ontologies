# Initial Ontology Design — Research and History

> Status: **Historical design research.** Not normative and not per-fact evidence.
> This file preserves the source inventory, the original proposed repository layout,
> and the initial implementation plan that informed `docs/spec.md` v0.1. It is kept
> for rationale and traceability only. For current, enforced behavior read
> `docs/spec.md`; for live sequencing read `docs/roadmap.md`.

The statements below describe the repository and plan **as observed during the
original research pass (2026-05-28)**. Several no longer hold — the repository is no
longer empty, tooling ships under `scripts/` rather than the proposed `tools/`, and
the early phases of the plan are delivered. Nothing here should be read as current
repository state or as evidence for a canonical ontology fact. Canonical facts cite
their own evidence in each module's `evidence_sources` (see `docs/spec.md` §9).

---

## 1. Source registry inspected for the draft

The following sources were inspected while drafting the spec. These are references
for the spec's design, not a replacement for per-fact evidence in ontology modules.

### 1.1 Repositories verified through GitHub (as observed 2026-05-28)

- `sabnanikl-dev/client-ontologies`
  - URL: `https://github.com/sabnanikl-dev/client-ontologies`
  - Visibility: public
  - State observed at the time: empty repository / no initial commits yet.
    (**Historical:** the repository has since been implemented — this observation is
    retained only to show what the spec was drafted against.)
- `sabnanikl-dev/Femme-Events-Website`
  - URL: `https://github.com/sabnanikl-dev/Femme-Events-Website`
  - Visibility: public
  - Description observed: `Femme Website`
  - Default branch: `main`
- `sabnanikl-dev/Femme-visibility`
  - URL: `https://github.com/sabnanikl-dev/Femme-visibility`
  - Visibility: private
  - Description observed: `will be used for femme visibility and reusable operations`
  - Default branch: `main`
- `sabnanikl-dev/jmd-6-holding-page-harness`
  - URL: `https://github.com/sabnanikl-dev/jmd-6-holding-page-harness`
  - Visibility: private
  - Description observed: `JMD-6 holding page coding harness and spec`
  - Default branch: `main`

### 1.2 Obsidian/wiki sources inspected

- `/Users/creator/obsidian-vault/hermes-brain/wiki/consultancy/clients/JMD/Client JMD Menswear.md`
- `/Users/creator/obsidian-vault/hermes-brain/wiki/femme-events/Femme Events Overview.md`
- `/Users/creator/obsidian-vault/hermes-brain/wiki/femme-events/Femme Events Brand Guide.md`

### 1.3 Local project documents inspected

- `/Users/creator/projects/consultancy/JMD-Menswear/deliverables/JMD-Website/docs/research/inventory-backend-automation-plan.md`
- `/Users/creator/projects/femme-events/visibility/Femme-visibility/docs/femme-events/local-seo-source-of-truth.md`
- `/Users/creator/projects/Femme-Events-Website/README.md`
- `/Users/creator/projects/Femme-Events-Website/AGENTS.md`
- `/Users/creator/projects/femme-events/visibility/Femme-visibility/AGENTS.md`
- `/Users/creator/projects/consultancy/JMD-Menswear/deliverables/JMD-Website/AGENTS.md`

### 1.4 Linear issues inspected via GraphQL search

Relevant issues observed at the time (planning/history context only; the spec did
not and does not mutate Linear):

- `PAPI-68` — Create Femme Events client operating ontology v0
- `PAPI-69` — Create ontology-management skill for client operating ontologies
- `PAPI-70` — Audit Femme ontology source material and workstreams
- `PAPI-71` — Author canonical Femme ontology YAML modules
- `PAPI-72` — Create Femme website and visibility ontology projections
- `JMD-30` — Create JMD Menswear client operating ontology v0
- `JMD-23` — Build deterministic Google Drive to Sanity photo automation for JMD showroom images
- `PAPI-44` — Task 0.3 — Create local SEO source of truth and claims guardrails
- `PAPI-56` — Review and approve Femme local SEO source of truth
- `JMD-20` — Verify JMD business data for landing page

---

## 2. Originally proposed repository layout (superseded)

The spec's first draft proposed the tree below, including `tools/`, `templates/`, and
per-client `handoff/` directories. **The live repository does not implement this
layout.** Live tooling ships under `scripts/` (`ontology_loader.py`,
`validate_ontology.py`, `check_rules.py`, `export_sqlite.py`); there is no `tools/`,
`templates/`, or `handoff/` directory yet. `templates/` and `handoff/` remain
**proposed** ideas, not current structure. See `README.md` and `docs/spec.md` §4 for
the current layout.

```text
client-ontologies/
  README.md
  docs/
    spec.md
    decisions/
      ADR-0001-authoring-format.md
  schemas/
    ontology.schema.json
    defs.schema.json
    evidence.schema.json
    rule.schema.json
    client.schema.json
    manifest.schema.json
    module.schema.json
    projection.schema.json
  templates/
    cms-website/
      module.yaml
      handoff.md
    local-visibility/
      module.yaml
      handoff.md
    inventory-image-automation/
      module.yaml
      handoff.md
    wedding-event-services/
      module.yaml
    formalwear-retail-showroom/
      module.yaml
  clients/
    femme-events/
      client.yaml
      ontology.yaml
      modules/
        website.yaml
        cms.yaml
        brand-content.yaml
        local-visibility.yaml
        inquiry-ops.yaml
        approvals.yaml
      projections/
        website-repo.yaml
        visibility-repo.yaml
        handoff.yaml
      handoff/
        glossary.md
        website-maintenance.md
        local-visibility-maintenance.md
        cms-data-dictionary.md
    jmd-menswear/
      client.yaml
      ontology.yaml
      modules/
        website-showroom.yaml
        inventory-images.yaml
        local-visibility.yaml
        approvals.yaml
        reporting.yaml
        content-engine.yaml
      projections/
        website-repo.yaml
        inventory-automation.yaml
        handoff.yaml
      handoff/
        glossary.md
        inventory-workflow.md
        website-maintenance.md
        cms-data-dictionary.md
  tools/
    validate_ontology.py
    generate_projection.py
    generate_handoff.py
    export_sqlite.py
```

Notable differences from the delivered repository:

- Tooling lives in `scripts/`, not `tools/`, and the shipped scripts are
  `ontology_loader.py`, `validate_ontology.py`, `check_rules.py`, and
  `export_sqlite.py`. `generate_projection.py` / `generate_handoff.py` were not built;
  projection generation and client-safe handoff packaging are tracked as open issues.
- Live module filenames are workstream-oriented and smaller than the sketch: Femme has
  `brand.yaml`, `website.yaml`, `local-visibility.yaml`, `operations.yaml`; JMD has
  `brand.yaml`, `website.yaml`, `inventory-images.yaml`, `operations.yaml`.
- `templates/` and per-client `handoff/` directories do not exist yet.

---

## 3. Initial implementation plan (largely delivered)

The spec's original "next sequence" is retained here for history. It was explicitly
**not** execution approval, and its early phases are now delivered. The live,
authoritative sequencing is `docs/roadmap.md`; open issues remain the implementation
contracts.

### Phase 1 — Repository foundation (delivered)

- Add `schemas/` with initial JSON Schemas. *(Delivered — split per-kind schemas.)*
- Add the ontology validator. *(Delivered as `scripts/validate_ontology.py`, not the
  proposed `tools/validate_ontology.py`.)*
- Add `templates/` for `cms-website`, `local-visibility`, and
  `inventory-image-automation`. *(Not delivered; remains proposed.)*
- Add a no-secret validation check. *(Delivered in the validator's cross-reference pass.)*

### Phase 2 — Femme v0 ontology (delivered, different module split)

- Add `clients/femme-events/client.yaml`. *(Delivered.)*
- Add modules. *(Delivered as `brand.yaml`, `website.yaml`, `local-visibility.yaml`,
  `operations.yaml` — not the `cms.yaml` / `brand-content.yaml` / `inquiry-ops.yaml` /
  `approvals.yaml` split sketched here.)*
- Add projections. *(Delivered as `agent-context.yaml`, `website-build.yaml`,
  `local-seo.yaml`.)*

### Phase 3 — JMD v0 ontology (delivered, different module split)

- Add `clients/jmd-menswear/client.yaml`. *(Delivered.)*
- Add modules. *(Delivered as `brand.yaml`, `website.yaml`, `inventory-images.yaml`,
  `operations.yaml`.)*
- Add projections. *(Delivered as `agent-context.yaml`, `website-build.yaml`,
  `inventory-workflow.yaml`.)*

### Phase 4 — Projection and handoff generation (open)

- Generate repo-specific projections into `clients/*/projections/`. *(Projections are
  hand-authored today; deterministic generation is not built.)*
- Generate curated handoff docs into `clients/*/handoff/`. *(Open — tracked as an issue;
  no `handoff/` directory exists yet.)*
- Do not copy projections into client implementation repos until reviewed. *(Still the
  standing policy.)*
