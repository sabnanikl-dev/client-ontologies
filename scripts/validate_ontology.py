#!/usr/bin/env python3
"""Deterministic validator for client ontology YAML files.

Uses Ruby's stdlib YAML parser so the repo does not require PyYAML/jsonschema.
The JSON Schema in schemas/ontology.schema.json documents the contract; this
script enforces repo-specific cross-reference and evidence rules.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
PUBLIC_OR_ENFORCED_STATUSES = {"active", "approved", "owner_reviewed_internal", "prohibited"}
SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"-----BEGIN (RSA |OPENSSH |EC |)PRIVATE KEY-----"),
]
SENSITIVE_FIELD_RE = re.compile(r"(?i)(password|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|client[_-]?secret)")
ALLOWED_SENSITIVE_TERMS = {
    "secret_policy",
    "No credentials, OAuth tokens, raw private exports, or payment details.",
    "No credentials, OAuth tokens, raw client exports, private address data, API tokens, or payment details.",
    "No credentials, private keys, OAuth tokens, raw Drive exports, customer personal data, or payment details.",
}
REQUIRED = {
    "client": {"schema_version", "kind", "id", "name", "status", "source_registry", "privacy", "workstreams"},
    "ontology": {"schema_version", "kind", "id", "client_id", "status", "modules", "projections"},
    "ontology_module": {"schema_version", "kind", "id", "title", "client_id", "status", "workstreams", "evidence_sources", "entities", "relationships", "rules"},
    "projection": {"schema_version", "kind", "id", "client_id", "status", "projection_target", "includes", "evidence_sources"},
}


def parse_yaml(path: Path) -> dict[str, Any]:
    code = "require 'yaml'; require 'json'; obj = YAML.load_file(ARGV[0]); puts JSON.generate(obj)"
    result = subprocess.run(["ruby", "-e", code, str(path)], text=True, capture_output=True)
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or result.stdout.strip())
    data = json.loads(result.stdout or "{}")
    if not isinstance(data, dict):
        raise ValueError("root must be a mapping")
    return data


def iter_yaml(root: Path) -> list[Path]:
    # Manifests load first so they are the entry point that pins module/projection membership.
    return [
        *sorted(root.glob("clients/*/ontology.yaml")),
        *sorted(root.glob("clients/*/client.yaml")),
        *sorted(root.glob("clients/*/modules/*.yaml")),
        *sorted(root.glob("clients/*/projections/*.yaml")),
    ]


def evidence_refs(value: Any):
    if isinstance(value, dict):
        if "source_id" in value:
            yield value["source_id"]
        for child in value.values():
            yield from evidence_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from evidence_refs(child)


def ids_in_file(data: dict[str, Any]) -> list[str]:
    """Return ontology object IDs, excluding evidence source IDs.

    Source IDs are intentionally local to a file's source registry so modules can
    reuse stable names like `femme-local-seo-sot` without creating false global
    duplicate failures.
    """
    ids = [data.get("id")]
    for key in ("entities", "relationships", "rules", "claims", "approval_gates", "state_machines"):
        for item in data.get(key, []) or []:
            if isinstance(item, dict) and item.get("id"):
                ids.append(item["id"])
    return [i for i in ids if i]


def scan_sensitive(path: Path, data: Any, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            errors.append(f"{path}: possible secret pattern matched: {pat.pattern}")

    def walk(value: Any, trail: str = "") -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                key_path = f"{trail}.{k}" if trail else str(k)
                if SENSITIVE_FIELD_RE.search(str(k)) and str(v) not in ALLOWED_SENSITIVE_TERMS:
                    errors.append(f"{path}: sensitive-looking field name needs removal or explicit safe wording: {key_path}")
                walk(v, key_path)
        elif isinstance(value, list):
            for idx, child in enumerate(value):
                walk(child, f"{trail}[{idx}]")
    walk(data)


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    docs: dict[Path, dict[str, Any]] = {}
    for path in iter_yaml(root):
        try:
            docs[path] = parse_yaml(path)
        except Exception as exc:
            errors.append(f"{path}: YAML parse failed: {exc}")
    all_ids: Counter[str] = Counter()
    module_ids: set[str] = set()
    entity_ids: set[str] = set()
    rule_ids: set[str] = set()
    client_ids: set[str] = set()

    for path, data in docs.items():
        kind = data.get("kind")
        if kind not in REQUIRED:
            errors.append(f"{path}: unknown or missing kind: {kind!r}")
            continue
        missing = sorted(REQUIRED[kind] - set(data))
        if missing:
            errors.append(f"{path}: missing required fields: {missing}")
        for id_value in ids_in_file(data):
            if not isinstance(id_value, str) or not ID_RE.match(id_value):
                errors.append(f"{path}: invalid ID: {id_value!r}")
            all_ids[id_value] += 1
        scan_sensitive(path, data, errors)
        if kind == "client":
            client_ids.add(data.get("id"))
        elif kind == "ontology_module":
            module_ids.add(data.get("id"))
            for ent in data.get("entities", []) or []:
                if isinstance(ent, dict): entity_ids.add(ent.get("id"))
            for rule in data.get("rules", []) or []:
                if isinstance(rule, dict): rule_ids.add(rule.get("id"))
        elif kind == "projection":
            pass

    for id_value, count in all_ids.items():
        if count > 1:
            errors.append(f"duplicate ID across ontology files: {id_value}")

    # Manifest-first: every client directory must carry its ontology.yaml entry point.
    for client_yaml in sorted(root.glob("clients/*/client.yaml")):
        manifest = client_yaml.parent / "ontology.yaml"
        if not manifest.exists():
            errors.append(f"{client_yaml.parent}: missing ontology.yaml manifest (required entry point for each client)")
        elif docs.get(manifest, {}).get("kind") != "ontology":
            errors.append(f"{manifest}: client manifest must be kind: ontology")

    docs_by_resolved = {p.resolve(): d for p, d in docs.items()}

    for path, data in docs.items():
        kind = data.get("kind")
        client_id = data.get("client_id") or data.get("id")
        if kind in {"ontology", "ontology_module", "projection"} and client_id not in client_ids:
            errors.append(f"{path}: client_id does not match an existing client: {client_id}")
        if data.get("id") and client_id and not data.get("id").startswith(str(client_id)):
            errors.append(f"{path}: ID should be namespaced with client id {client_id}: {data.get('id')}")

        sources_key = "source_registry" if kind == "client" else "evidence_sources"
        source_ids = {src.get("id") for src in data.get(sources_key, []) or [] if isinstance(src, dict)}
        # Manifests are structural pointers, not semantic claims, so they carry no evidence_sources.
        if kind not in {"client", "ontology"} and not source_ids:
            errors.append(f"{path}: evidence_sources must not be empty")
        for ref in evidence_refs(data):
            if ref not in source_ids:
                errors.append(f"{path}: evidence source_id references unknown local source: {ref}")

        if kind == "ontology_module":
            local_entities = {ent.get("id") for ent in data.get("entities", []) or [] if isinstance(ent, dict)}
            local_sources = source_ids
            local_ids = ids_in_file(data)
            if len(local_ids) != len(set(local_ids)):
                dupes = [k for k, v in Counter(local_ids).items() if v > 1]
                errors.append(f"{path}: duplicate IDs within file: {dupes}")
            for ent in data.get("entities", []) or []:
                if not isinstance(ent, dict):
                    errors.append(f"{path}: entity entry must be a mapping")
                    continue
                if ent.get("status") in PUBLIC_OR_ENFORCED_STATUSES and not ent.get("evidence"):
                    errors.append(f"{path}: active/approved entity lacks evidence: {ent.get('id')}")
                if ent.get("source_confidence") == "verified" and not ent.get("evidence"):
                    errors.append(f"{path}: verified entity lacks evidence: {ent.get('id')}")
            for rel in data.get("relationships", []) or []:
                if not isinstance(rel, dict):
                    errors.append(f"{path}: relationship entry must be a mapping")
                    continue
                for field in ("subject", "object"):
                    value = rel.get(field)
                    if value not in entity_ids:
                        errors.append(f"{path}: relationship {rel.get('id')} references unknown {field}: {value}")
                if rel.get("source_confidence") == "verified" and not rel.get("evidence"):
                    errors.append(f"{path}: verified relationship lacks evidence: {rel.get('id')}")
            for rule in data.get("rules", []) or []:
                if not isinstance(rule, dict):
                    errors.append(f"{path}: rule entry must be a mapping")
                    continue
                if rule.get("status") in PUBLIC_OR_ENFORCED_STATUSES and not rule.get("evidence"):
                    errors.append(f"{path}: active/approved/prohibited rule lacks evidence: {rule.get('id')}")
                if rule.get("source_confidence") == "verified" and not rule.get("evidence"):
                    errors.append(f"{path}: verified rule lacks evidence: {rule.get('id')}")
        elif kind == "projection":
            includes = data.get("includes") or {}
            for mod_id in includes.get("modules", []) or []:
                if mod_id not in module_ids:
                    errors.append(f"{path}: projection references unknown module: {mod_id}")
            for ent_id in includes.get("entities", []) or []:
                if ent_id.endswith(".*"):
                    prefix = ent_id[:-1]
                    if not any(e.startswith(prefix) for e in entity_ids):
                        errors.append(f"{path}: projection wildcard has no entity matches: {ent_id}")
                elif ent_id not in entity_ids:
                    errors.append(f"{path}: projection references unknown entity: {ent_id}")
            for rule_id in includes.get("rules", []) or []:
                if rule_id.endswith(".*"):
                    prefix = rule_id[:-1]
                    if not any(r.startswith(prefix) for r in rule_ids):
                        errors.append(f"{path}: projection wildcard has no rule matches: {rule_id}")
                elif rule_id not in rule_ids:
                    errors.append(f"{path}: projection references unknown rule: {rule_id}")
        elif kind == "ontology":
            client_dir = path.parent
            client_root = client_dir.resolve()
            expected_kind = {"modules": "ontology_module", "projections": "projection"}
            for section in ("modules", "projections"):
                want_kind = expected_kind[section]
                declared_paths: set[str] = set()
                for entry in data.get(section, []) or []:
                    if not isinstance(entry, dict):
                        errors.append(f"{path}: {section} entry must be a mapping")
                        continue
                    rel_path = entry.get("path")
                    decl_id = entry.get("id")
                    if not rel_path:
                        errors.append(f"{path}: {section} entry missing path (id={decl_id!r})")
                        continue
                    declared_paths.add(rel_path)
                    target = (client_dir / rel_path).resolve()
                    if not target.is_relative_to(client_root):
                        errors.append(f"{path}: manifest {section} entry escapes client directory: {rel_path}")
                        continue
                    ref = docs_by_resolved.get(target)
                    if ref is None:
                        errors.append(f"{path}: manifest references missing or unparsed {section} file: {rel_path}")
                        continue
                    actual_id = ref.get("id")
                    if decl_id != actual_id:
                        errors.append(f"{path}: manifest {section} id mismatch for {rel_path}: declared {decl_id!r}, file declares {actual_id!r}")
                    if ref.get("kind") != want_kind:
                        errors.append(f"{path}: manifest {section} entry {rel_path} must reference kind {want_kind!r}, file declares {ref.get('kind')!r}")
                    if ref.get("client_id") != data.get("client_id"):
                        errors.append(f"{path}: manifest {section} entry {rel_path} client_id mismatch: manifest {data.get('client_id')!r}, file {ref.get('client_id')!r}")
                present = {f"{section}/{p.name}" for p in (client_dir / section).glob("*.yaml")}
                for unregistered in sorted(present - declared_paths):
                    errors.append(f"{path}: {section} file not registered in manifest: {unregistered}")
    return errors


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    errors = validate(root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"ontology validation failed: {len(errors)} error(s)", file=sys.stderr)
        return 1
    print("ontology validation passed")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
