"""Shared local JSON Schema registry for the reference adapters."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).parent


def registry():
    resources = []
    for base in [ROOT / "upstream/schemas", ROOT / "schemas"]:
        for path in base.rglob("*.json"):
            data = json.loads(path.read_text())
            if "$id" in data:
                resources.append((data["$id"], Resource.from_contents(data)))
    return Registry().with_resources(resources)


REGISTRY = registry()


def validate(payload, name):
    schema = json.loads((ROOT / "schemas" / name).read_text())
    Draft202012Validator(
        schema, registry=REGISTRY, format_checker=FormatChecker()
    ).validate(payload)


def validate_declarations(profile, kind):
    uri = f"https://ucp.dev/schemas/capability.json#/$defs/{kind}_schema"
    validator = Draft202012Validator(
        {"$ref": uri}, registry=REGISTRY, format_checker=FormatChecker()
    )
    for entries in profile.values():
        for entry in entries:
            validator.validate(entry)
