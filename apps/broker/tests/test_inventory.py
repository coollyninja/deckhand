"""Protected-resource inventory loading and matching."""

from pathlib import Path

import pytest
from deckhand.inventory import InventoryError, load_protected_inventory
from deckhand.models import Target


def test_absent_path_yields_empty_inventory() -> None:
    inv = load_protected_inventory(None)
    assert not inv.is_protected(Target(type="pve_vm", id="anything"))


def test_protects_specific_target(tmp_path: Path) -> None:
    path = tmp_path / "inv.yaml"
    path.write_text(
        "protected:\n  targets:\n    - {type: pve_vm, id: broker-vm}\n", encoding="utf-8"
    )
    inv = load_protected_inventory(path)
    assert inv.is_protected(Target(type="pve_vm", id="broker-vm"))
    assert not inv.is_protected(Target(type="pve_vm", id="other-vm"))


def test_protects_entire_type(tmp_path: Path) -> None:
    path = tmp_path / "inv.yaml"
    path.write_text("protected:\n  types:\n    - physical_estop\n", encoding="utf-8")
    inv = load_protected_inventory(path)
    assert inv.is_protected(Target(type="physical_estop", id="anything"))
    assert not inv.is_protected(Target(type="pve_vm", id="anything"))


def test_json_inventory(tmp_path: Path) -> None:
    path = tmp_path / "inv.json"
    path.write_text('{"protected": {"targets": [{"type": "dns_zone", "id": "primary"}]}}')
    inv = load_protected_inventory(path)
    assert inv.is_protected(Target(type="dns_zone", id="primary"))


def test_example_file_loads(tmp_path: Path) -> None:
    example = Path(__file__).parents[3] / "config/protected-inventory.example.yaml"
    inv = load_protected_inventory(example)
    # The shipped example protects the physical_estop type.
    assert inv.is_protected(Target(type="physical_estop", id="e1"))


def test_malformed_inventory_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("protected:\n  targets:\n    - {type: pve_vm}\n", encoding="utf-8")
    with pytest.raises(InventoryError):
        load_protected_inventory(path)
