import argparse
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="sdctl", description="Deckhand operator CLI")
    root.add_argument("--broker", default="https://deckhand.invalid")
    root.add_argument("--client", default="sdctl")
    root.add_argument("--cert", type=Path)
    root.add_argument("--key", type=Path)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("actions")
    status_command = commands.add_parser("status")
    status_command.add_argument("domain", nargs="?")
    for name in ("plan", "execute"):
        action_command = commands.add_parser(name)
        action_command.add_argument("action_id")
        action_command.add_argument("target_type")
        action_command.add_argument("target_id")
        action_command.add_argument("--version", type=int, default=1)
        action_command.add_argument("--parameters", default="{}")
        action_command.add_argument("--confirmation-token")
    job_command = commands.add_parser("job")
    job_command.add_argument("job_id")
    return root


def request_payload(args: argparse.Namespace) -> dict[str, Any]:
    try:
        parameters = json.loads(args.parameters)
    except json.JSONDecodeError as error:
        raise SystemExit(f"invalid --parameters JSON: {error}") from error
    if not isinstance(parameters, dict):
        raise SystemExit("--parameters must be a JSON object")
    return {
        "action_id": args.action_id,
        "action_version": args.version,
        "target": {"type": args.target_type, "id": args.target_id},
        "parameters": parameters,
        "context": {"client": args.client},
        "idempotency_key": str(uuid4()),
        "dry_run": args.command == "plan",
        "confirmation_token": args.confirmation_token,
    }


def run() -> None:
    args = parser().parse_args()
    certificate = (str(args.cert), str(args.key)) if args.cert and args.key else None
    try:
        with httpx.Client(base_url=args.broker, cert=certificate, timeout=15) as client:
            if args.command == "actions":
                response = client.get("/v1/actions")
            elif args.command == "status":
                path = f"/v1/status/{args.domain}" if args.domain else "/v1/status/summary"
                response = client.get(path)
            elif args.command in {"plan", "execute"}:
                response = client.post(
                    f"/v1/actions/{args.action_id}:{args.command}", json=request_payload(args)
                )
            else:
                response = client.get(f"/v1/jobs/{args.job_id}")
            response.raise_for_status()
    except httpx.HTTPError as error:
        print(f"sdctl: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(json.dumps(response.json(), indent=2, sort_keys=True))


if __name__ == "__main__":
    run()
