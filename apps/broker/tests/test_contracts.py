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


def test_wasm_out_of_process_activation_matches_schema_and_model() -> None:
    # The out-of-process wasm transport: the wasm block gains an optional `socket`
    # sub-object carrying the host transport connection shape. It must validate against
    # both the published JSON schema and the pydantic PluginConfiguration model.
    from deckhand.plugins import PluginConfiguration

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
                    "mode": "wasm",
                    "wasm": {
                        "data_dir": "/var/lib/deckhand/plugins/dh-example",
                        "robot": "up-robot",
                        "capability": "dh-example-http",
                        "socket": {
                            "socket_path": "/run/deckhand/plugins/dh-example/plugin.sock",
                            "expected_uid": 24001,
                            "artifact_path": "/opt/deckhand/plugins/dh-example/component.wasm",
                            "signature_path": "/opt/deckhand/plugins/dh-example/component.wasm.sig",
                            "public_key_path": "/etc/deckhand/trust/example-publisher.pem",
                        },
                    },
                },
            }
        },
    }
    Draft202012Validator(schema).validate(configuration)
    parsed = PluginConfiguration.model_validate(configuration)
    assert parsed.plugins["dh-example"].runtime.wasm is not None
    assert parsed.plugins["dh-example"].runtime.wasm.socket is not None


def test_pre_wasm_site_lock_and_config_still_validate() -> None:
    # ADR-0005 site-overlay upgrade contract: the wasm additions are additive within
    # schema_version 1, so a private deckhand-site-* overlay written BEFORE wasm
    # existed (only builtin/python, no wasm block) validates unchanged and is never
    # force-upgraded. This pins that contract — a future schema change that breaks a
    # pre-wasm overlay must bump schema_version, not silently reject it.
    lock_schema = json.loads(
        Path("packages/contracts/plugin-lock.schema.json").read_text(encoding="utf-8")
    )
    config_schema = json.loads(
        Path("packages/contracts/plugin-configuration.schema.json").read_text(encoding="utf-8")
    )
    pre_wasm_lock = {
        "schema_version": 1,
        "plugins": [
            {"id": "dh-http-status", "version": "0.1.0", "source": "python"},
            {"id": "dh-proxmox", "version": "0.1.0", "source": "python"},
        ],
    }
    pre_wasm_config = {
        "schema_version": 1,
        "plugins": {
            # A pre-wasm overlay: in_process activations that name no wasm block at
            # all. The point is that the wasm additions never force these to change.
            "dh-http-status": {"enabled": True, "config": {}},
            "dh-proxmox": {"enabled": True, "config": {}, "runtime": {"mode": "in_process"}},
        },
    }
    Draft202012Validator(lock_schema).validate(pre_wasm_lock)
    Draft202012Validator(config_schema).validate(pre_wasm_config)
