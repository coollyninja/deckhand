import json
from pathlib import Path

from jsonschema import Draft202012Validator


def test_published_contract_schemas_are_valid() -> None:
    for schema_path in sorted(Path("packages/contracts").glob("*.schema.json")):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
