import hashlib
import json
from typing import Any

from .models import ActionRequest


def request_payload(request: ActionRequest) -> dict[str, Any]:
    """Return the authority-bearing request fields in canonical form."""
    payload = request.model_dump(mode="json", exclude={"confirmation_token"})
    return payload


def request_digest(request: ActionRequest) -> str:
    encoded = json.dumps(request_payload(request), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
