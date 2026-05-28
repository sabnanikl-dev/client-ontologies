# Client Ontologies

Agent-agnostic operating ontologies for client workstreams, content models, approval rules, repo projections, and handoff documentation.

## Current status

This repository is initialized with a draft specification:

- [`docs/spec.md`](docs/spec.md) — Client Operating Ontology Spec v0.1

## Design principles

- Canonical ontology source is version-controlled text, not an agent's private memory.
- Ontologies are agent-agnostic and tool-portable.
- Client-specific facts must cite a verified source path, URL, issue, or approval record.
- Public/client-facing handoff exports must not leak internal execution notes, credentials, or private context.
- Runtime stores such as SQLite, Postgres, Sanity, or graph databases are projections/consumers, not the canonical authoring surface in v0.
