import hashlib
import json
from typing import Any

from .models import ActionRequest


def request_payload(request: ActionRequest) -> dict[str, Any]:
    """Return the full replay-identity request fields in canonical form.

    Used for the idempotency/replay digest: two submissions are "the same request"
    only when every meaningful field (target, parameters, dry_run, context, key)
    matches. The confirmation token is excluded because it is proof-of-approval,
    not part of the request's identity.
    """
    return request.model_dump(mode="json", exclude={"confirmation_token", "confirmation_response"})


def request_digest(request: ActionRequest) -> str:
    """Replay/idempotency digest over the full request payload."""
    encoded = json.dumps(request_payload(request), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def confirmation_payload(request: ActionRequest) -> dict[str, Any]:
    """Return only the AUTHORITY-BEARING fields a confirmation must bind.

    A confirmation approves *what will happen* — the action, its version, the
    target, and the parameters. It must NOT bind transport-shaped fields
    (``dry_run``, ``idempotency_key``, ``confirmation_token``, ``context``),
    because a client legitimately plans with ``dry_run=true`` and then executes
    with ``dry_run=false`` under a fresh idempotency key. Binding those fields is
    what made the shipped confirm→execute flow impossible; excluding them lets a
    plan-time approval authorize the matching execute.
    """
    return {
        "action_id": request.action_id,
        "action_version": request.action_version,
        "target": request.target.model_dump(mode="json"),
        "parameters": request.parameters,
    }


def confirmation_digest(request: ActionRequest) -> str:
    """Digest of the authority-bearing subset a confirmation is bound to."""
    encoded = json.dumps(confirmation_payload(request), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
