#!/usr/bin/env python3
"""Export canonical ontology YAML into a local SQLite runtime database.

YAML remains the source of truth. This script creates a consumer-friendly
SQLite projection for agents/scripts that need fast local lookups.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from ontology_loader import iter_yaml, load_documents, parse_yaml


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS evidence;
        DROP TABLE IF EXISTS sources;
        DROP TABLE IF EXISTS projections;
        DROP TABLE IF EXISTS rules;
        DROP TABLE IF EXISTS relationships;
        DROP TABLE IF EXISTS entities;
        DROP TABLE IF EXISTS modules;
        DROP TABLE IF EXISTS manifests;
        DROP TABLE IF EXISTS clients;

        CREATE TABLE manifests (
          client_id TEXT NOT NULL,
          manifest_id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          raw_json TEXT NOT NULL
        );

        CREATE TABLE clients (
          client_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          status TEXT NOT NULL,
          client_type TEXT,
          source_path TEXT NOT NULL,
          raw_json TEXT NOT NULL
        );

        CREATE TABLE modules (
          client_id TEXT NOT NULL,
          module_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          status TEXT NOT NULL,
          source_path TEXT NOT NULL,
          raw_json TEXT NOT NULL
        );

        CREATE TABLE entities (
          client_id TEXT NOT NULL,
          module_id TEXT NOT NULL,
          entity_id TEXT PRIMARY KEY,
          label TEXT NOT NULL,
          entity_type TEXT NOT NULL,
          status TEXT,
          source_confidence TEXT,
          public_facing INTEGER NOT NULL DEFAULT 0,
          raw_json TEXT NOT NULL
        );

        CREATE TABLE relationships (
          client_id TEXT NOT NULL,
          module_id TEXT NOT NULL,
          relationship_id TEXT PRIMARY KEY,
          subject TEXT NOT NULL,
          predicate TEXT NOT NULL,
          object TEXT NOT NULL,
          source_confidence TEXT,
          raw_json TEXT NOT NULL
        );

        CREATE TABLE rules (
          client_id TEXT NOT NULL,
          module_id TEXT NOT NULL,
          rule_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          status TEXT NOT NULL,
          severity TEXT NOT NULL,
          rule_type TEXT NOT NULL,
          statement TEXT NOT NULL,
          source_confidence TEXT,
          raw_json TEXT NOT NULL
        );

        CREATE TABLE projections (
          client_id TEXT NOT NULL,
          projection_id TEXT PRIMARY KEY,
          target_type TEXT,
          status TEXT NOT NULL,
          includes_json TEXT NOT NULL,
          source_path TEXT NOT NULL,
          raw_json TEXT NOT NULL
        );

        CREATE TABLE sources (
          client_id TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          source_id TEXT NOT NULL,
          source_type TEXT NOT NULL,
          path TEXT,
          url TEXT,
          identifier TEXT,
          description TEXT,
          raw_json TEXT NOT NULL,
          PRIMARY KEY (owner_id, source_id)
        );

        CREATE TABLE evidence (
          client_id TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          item_id TEXT NOT NULL,
          item_kind TEXT NOT NULL,
          source_id TEXT NOT NULL,
          lines TEXT,
          note TEXT,
          raw_json TEXT NOT NULL
        );

        CREATE INDEX idx_modules_client ON modules(client_id);
        CREATE INDEX idx_entities_client_type ON entities(client_id, entity_type);
        CREATE INDEX idx_rules_client_status ON rules(client_id, status);
        CREATE INDEX idx_rules_client_severity ON rules(client_id, severity);
        CREATE INDEX idx_evidence_item ON evidence(item_id);
        """
    )


def insert_sources(conn: sqlite3.Connection, client_id: str, owner_id: str, sources: list[dict[str, Any]]) -> None:
    for src in sources or []:
        conn.execute(
            "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (client_id, owner_id, src.get("id"), src.get("type"), src.get("path"), src.get("url"), src.get("identifier"), src.get("description"), dump(src)),
        )


def insert_evidence(conn: sqlite3.Connection, client_id: str, owner_id: str, item_id: str, item_kind: str, evidence: list[dict[str, Any]]) -> None:
    for ev in evidence or []:
        conn.execute(
            "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (client_id, owner_id, item_id, item_kind, ev.get("source_id"), ev.get("lines"), ev.get("note"), dump(ev)),
        )


def export(root: Path, output: Path, paths: list[Path] | None = None) -> None:
    """Export canonical YAML into ``output``.

    By default this covers the exact same file set the validator gates on
    (``load_documents(root)``, manifest-first). When ``paths`` is given, only
    those files are parsed and exported — a client/projection-directed load that
    reuses the identical shared parser (``parse_yaml``) and table shapes, so a
    caller (e.g. the competency runner, issue #31) can build a scoped export
    without scanning every client's YAML. ``paths`` must stay under ``root``.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    conn = sqlite3.connect(output)
    init_db(conn)

    # Enumerate via the shared loader (full export) unless the caller pinned an
    # explicit scoped file set. Either way parsing goes through parse_yaml.
    if paths is None:
        documents = load_documents(root)
    else:
        documents = {p: parse_yaml(p) for p in paths}
    for path, data in documents.items():
        kind = data.get("kind")
        if kind == "ontology":
            conn.execute(
                "INSERT INTO manifests VALUES (?, ?, ?, ?)",
                (data.get("client_id"), data["id"], data.get("status"), dump(data)),
            )
        elif kind == "client":
            client_id = data["id"]
            conn.execute("INSERT INTO clients VALUES (?, ?, ?, ?, ?, ?)", (client_id, data.get("name"), data.get("status"), data.get("client_type"), str(path.relative_to(root)), dump(data)))
            insert_sources(conn, client_id, client_id, data.get("source_registry", []))
        elif kind == "ontology_module":
            client_id = data["client_id"]
            module_id = data["id"]
            conn.execute("INSERT INTO modules VALUES (?, ?, ?, ?, ?, ?)", (client_id, module_id, data.get("title"), data.get("status"), str(path.relative_to(root)), dump(data)))
            insert_sources(conn, client_id, module_id, data.get("evidence_sources", []))
            for ent in data.get("entities", []) or []:
                conn.execute("INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (client_id, module_id, ent.get("id"), ent.get("label"), ent.get("entity_type"), ent.get("status"), ent.get("source_confidence"), 1 if ent.get("public_facing") else 0, dump(ent)))
                insert_evidence(conn, client_id, module_id, ent.get("id"), "entity", ent.get("evidence", []))
            for rel in data.get("relationships", []) or []:
                conn.execute("INSERT INTO relationships VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (client_id, module_id, rel.get("id"), rel.get("subject"), rel.get("predicate"), rel.get("object"), rel.get("source_confidence"), dump(rel)))
                insert_evidence(conn, client_id, module_id, rel.get("id"), "relationship", rel.get("evidence", []))
            for rule in data.get("rules", []) or []:
                conn.execute("INSERT INTO rules VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (client_id, module_id, rule.get("id"), rule.get("title"), rule.get("status"), rule.get("severity"), rule.get("rule_type"), rule.get("statement"), rule.get("source_confidence"), dump(rule)))
                insert_evidence(conn, client_id, module_id, rule.get("id"), "rule", rule.get("evidence", []))
        elif kind == "projection":
            client_id = data["client_id"]
            projection_id = data["id"]
            target = data.get("projection_target") or {}
            conn.execute("INSERT INTO projections VALUES (?, ?, ?, ?, ?, ?, ?)", (client_id, projection_id, target.get("type"), data.get("status"), dump(data.get("includes", {})), str(path.relative_to(root)), dump(data)))
            insert_sources(conn, client_id, projection_id, data.get("evidence_sources", []))
    conn.commit()
    conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="Repository root")
    parser.add_argument("--output", default="build/client-ontologies.sqlite", help="SQLite output path")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    output = (root / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    export(root, output)
    print(f"exported SQLite ontology to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
