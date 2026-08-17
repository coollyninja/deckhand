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


def test_sidecar_activation_matches_published_schema() -> None:
    schema = json.loads(
        Path("packages/contracts/plugin-configuration.schema.json").read_text(encoding="utf-8")
    )
    configuration = {
        "schema_version": 1,
        "plugins": {
            "dh-example": {
                "enabled": True,
                "config": {},
                "runtime": {
                    "mode": "sidecar",
                    "timeout_seconds": 10,
                    "sidecar": {
                        "socket_path": "/run/deckhand/plugins/dh-example/plugin.sock",
                        "expected_uid": 24001,
                        "artifact_path": "/opt/deckhand/plugins/dh-example/current",
                        "signature_path": "/opt/deckhand/plugins/dh-example/current.sig",
                        "public_key_path": "/etc/deckhand/trust/example-publisher.pem",
                    },
                },
            }
        },
    }
    Draft202012Validator(schema).validate(configuration)
