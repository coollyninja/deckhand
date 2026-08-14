import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .models import ActionDefinition, ActionRequest


class CatalogError(ValueError):
    pass


class Catalog:
    def __init__(self, actions: list[ActionDefinition]) -> None:
        self._actions = {(action.id, action.version): action for action in actions}
        if len(self._actions) != len(actions):
            raise CatalogError("duplicate action ID/version")

    @classmethod
    def from_path(cls, path: Path, *, additional: tuple[ActionDefinition, ...] = ()) -> "Catalog":
        actions = list(additional)
        if path.exists():
            for file in sorted(path.glob("*.json")):
                raw = json.loads(file.read_text(encoding="utf-8"))
                actions.append(ActionDefinition.model_validate(raw))
        return cls(actions)

    def list_actions(self) -> list[ActionDefinition]:
        return sorted(self._actions.values(), key=lambda action: (action.id, action.version))

    def get(self, action_id: str, version: int) -> ActionDefinition:
        try:
            return self._actions[(action_id, version)]
        except KeyError as error:
            raise CatalogError(f"unknown action {action_id}@{version}") from error

    def validate_request(self, request: ActionRequest) -> ActionDefinition:
        action = self.get(request.action_id, request.action_version)
        if request.target.type not in action.target_types:
            raise CatalogError(f"target type {request.target.type!r} is not allowed")
        validator = Draft202012Validator(action.parameter_schema)
        errors = sorted(validator.iter_errors(request.parameters), key=lambda item: list(item.path))
        if errors:
            message = "; ".join(error.message for error in errors)
            raise CatalogError(f"invalid parameters: {message}")
        return action

    def serializable(self) -> list[dict[str, Any]]:
        return [action.model_dump(mode="json") for action in self.list_actions()]
