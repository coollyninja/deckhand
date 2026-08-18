"""Protected-resource inventory.

The public core owns the *mechanism* for marking a target protected; the actual
list of protected targets is topology-specific and supplied by the private site
overlay as a data file. This module loads that file and answers is_protected().

Protected targets must never be mutated from the deck (Appendix C baseline):
E-stops, quorum links, the broker's own VM, primary DNS/DHCP, UPS control,
storage membership, identity infrastructure, and so on. Marking a target
protected feeds ``target.protected = true`` into the policy input, which the
deny-by-default rego uses to refuse mutation regardless of other allowances.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import Target


class InventoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProtectedInventory:
    """A set of protected (type, id) pairs and protected target types.

    An entry can protect a specific target ("pve_vm:100") or an entire type
    ("physical_estop"), so a whole class of resources can be fenced off without
    enumerating every id.
    """

    protected_pairs: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    protected_types: frozenset[str] = field(default_factory=frozenset)

    def is_protected(self, target: Target) -> bool:
        if target.type in self.protected_types:
            return True
        return (target.type, target.id) in self.protected_pairs


def _coerce_entries(raw: Any) -> ProtectedInventory:
    if raw is None:
        return ProtectedInventory()
    if not isinstance(raw, dict):
        raise InventoryError("protected inventory root must be a mapping")
    protected = raw.get("protected", {})
    if not isinstance(protected, dict):
        raise InventoryError("'protected' must be a mapping of types and targets")

    types = protected.get("types", [])
    targets = protected.get("targets", [])
    if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
        raise InventoryError("'protected.types' must be a list of strings")
    if not isinstance(targets, list):
        raise InventoryError("'protected.targets' must be a list")

    pairs: set[tuple[str, str]] = set()
    for entry in targets:
        if not isinstance(entry, dict) or "type" not in entry or "id" not in entry:
            raise InventoryError("each protected target needs a 'type' and 'id'")
        pairs.add((str(entry["type"]), str(entry["id"])))

    return ProtectedInventory(
        protected_pairs=frozenset(pairs),
        protected_types=frozenset(types),
    )


def load_protected_inventory(path: Path | None) -> ProtectedInventory:
    """Load the protected inventory from a YAML or JSON file. An unset path yields
    an empty inventory (no target marked protected); mutation still requires
    explicit allowlisting in policy, so absence fails toward deny rather than
    silently unprotecting everything."""
    if path is None:
        return ProtectedInventory()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise InventoryError(f"protected inventory unavailable: {error}") from error
    try:
        raw = yaml.safe_load(text) if path.suffix in {".yaml", ".yml"} else json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as error:
        raise InventoryError("protected inventory is not valid YAML/JSON") from error
    return _coerce_entries(raw)
