import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


def test_published_contract_schemas_are_valid() -> None:
    for schema_path in sorted(Path("packages/contracts").glob("*.schema.json")):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)


def test_example_plugin_configuration_matches_published_schema() -> None:
    schema = json.loads(
        Path("packages/contracts/plugin-configuration.schema.json").read_text(encoding="utf-8")
    )
    configuration = yaml.safe_load(Path("config/plugins.example.yaml").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(configuration)
