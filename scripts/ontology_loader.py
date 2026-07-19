#!/usr/bin/env python3
"""Shared, dependency-free YAML loading for the ontology scripts.

This module is the single canonical entry point for reading canonical ontology
YAML. Both scripts/validate_ontology.py and scripts/export_sqlite.py import it so
they parse files the same way and enumerate the same set of files.

YAML is parsed through Ruby's stdlib parser (see CLAUDE.md's hidden-dependency
note), so the repo needs neither PyYAML nor jsonschema. Ruby must be on PATH.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

# Ruby one-liner: load the YAML file and re-emit it as JSON on stdout so Python
# can parse it with the stdlib json module. Kept here as the only copy.
_RUBY_YAML_TO_JSON = (
    "require 'yaml'; require 'json'; obj = YAML.load_file(ARGV[0]); puts JSON.generate(obj)"
)


def parse_yaml(path: Path) -> dict[str, Any]:
    """Parse a single YAML file into a dict via Ruby's stdlib YAML parser.

    Raises ValueError on a Ruby parse error or when the document root is not a
    mapping. The path is included in the message so callers can surface it.
    """
    result = subprocess.run(
        ["ruby", "-e", _RUBY_YAML_TO_JSON, str(path)],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError(f"{path}: {result.stderr.strip() or result.stdout.strip()}")
    data = json.loads(result.stdout or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a mapping")
    return data


def iter_yaml(root: Path) -> list[Path]:
    """Enumerate canonical ontology YAML files under ``root``, manifest-first.

    Manifests (``clients/*/ontology.yaml``) load first because they are the entry
    point that pins module/projection membership. Validator and exporter share
    this single ordering so they always operate on the same definition of "the
    repo".
    """
    return [
        *sorted(root.glob("clients/*/ontology.yaml")),
        *sorted(root.glob("clients/*/client.yaml")),
        *sorted(root.glob("clients/*/modules/*.yaml")),
        *sorted(root.glob("clients/*/projections/*.yaml")),
    ]


def load_documents(root: Path) -> dict[Path, dict[str, Any]]:
    """Parse every file from ``iter_yaml(root)`` into a Path -> document mapping.

    Insertion order matches ``iter_yaml`` (manifest-first). Raises ValueError on
    the first unparseable file; callers that need to collect per-file parse
    errors should iterate ``iter_yaml`` and call ``parse_yaml`` themselves.
    """
    return {path: parse_yaml(path) for path in iter_yaml(root)}
