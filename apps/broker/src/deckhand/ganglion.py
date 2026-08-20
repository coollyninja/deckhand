"""Ganglion WASM isolation tier (ADR-0005).

The third plugin isolation mode, ``wasm``: a Deckhand adapter runs as a signed
Ganglion WASM component under gang-wasm-host's no-ambient-authority capability
broker, invoked through the ``gang`` CLI. It sits behind the existing ``Adapter``
protocol, inside the ADR-0003 resilience wrapper, exactly where ``SidecarAdapter``
sits — so nothing above the adapter boundary (jobs, verification, reconciliation,
confirmation, OPA) changes.

The six lifecycle operations map to six named WASM exports (Ganglion v2.5
named-export invocation): the broker calls
``gang --data-dir <dir> run <robot> <cap> --export <op>``, passing the
Deckhand-owned action/request as a single JSON argument and parsing the WIT
``deckhand:adapter@1.0.0`` result. The component's egress goes through
``ganglion:http/egress`` (URL-allowlisted) and its secrets through credential
slots — declared in the signed manifest, enforced by Ganglion, invisible to
Deckhand. Deckhand pins the capability by exact digest in the plugin lock, mapped
to the Ganglion trust store.

Feature-flagged and fail-closed: ``DECKHAND_ALLOW_WASM_PLUGINS=false`` by default.
Sidecar protocol v1 is untouched; this tier coexists with it.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from .adapters import (
    AdapterCancellation,
    AdapterError,
    AdapterErrorKind,
    AdapterExecution,
    AdapterHealth,
    AdapterObservation,
    AdapterPlan,
    AdapterVerification,
    UnknownOutcome,
)
from .models import ActionDefinition, ActionRequest, RetryDisposition, StatusValue, StrictModel
from .plugin_api import PluginManifest

# Bound the structured result a component may return, defence-in-depth against a
# wedged or malicious component flooding the broker. Ganglion also caps its own
# http/egress responses (256 KiB); this bounds the lifecycle result envelope.
_MAX_RESULT_BYTES = 1_048_576

_EXPORTS = frozenset({"describe", "health", "plan", "execute", "observe", "verify", "cancel"})


class WasmDescription(StrictModel):
    """What a signed WASM component reports about itself at load — the manifest it
    declares plus its adapter and status-provider names. The Ganglion trust store
    and the exact-digest lock guarantee this description came from the pinned
    signed artifact, exactly as the sidecar handshake does."""

    manifest: PluginManifest
    adapters: list[str] = Field(default_factory=list)
    status_providers: list[str] = Field(default_factory=list)
    actions: list[ActionDefinition] = Field(default_factory=list)


class WasmError(AdapterError):
    """A transport/protocol failure invoking a WASM component through gang."""

    def __init__(self, message: str, *, kind: AdapterErrorKind = AdapterErrorKind.PROTOCOL) -> None:
        super().__init__(message, kind=kind, retry=RetryDisposition.SAFE)


class WasmConnection(StrictModel):
    """Everything the broker needs to invoke one signed WASM capability locally
    through the ``gang`` CLI. Real values live only in the private site overlay."""

    gang_binary: str = Field(default="gang", max_length=256)
    data_dir: Path
    robot: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    capability: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    invoke_timeout_seconds: float = Field(default=20.0, gt=0, le=120)

    @field_validator("gang_binary")
    @classmethod
    def validate_binary(cls, value: str) -> str:
        # Either an absolute path or a bare command name resolved on PATH; never a
        # relative path or a value carrying shell metacharacters.
        if value.startswith("/"):
            return value
        if "/" in value or any(c in value for c in " \t\n;|&$`"):
            raise ValueError("gang_binary must be an absolute path or a bare command name")
        return value

    @field_validator("data_dir")
    @classmethod
    def validate_data_dir(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("data_dir must be an absolute path")
        return value


class GanglionClient:
    """Invokes a signed WASM capability's named exports through ``gang``.

    Every argv element is a fixed flag or a validated, non-request-derived
    configuration value plus the fixed export name; the only request-derived data
    crosses as a single JSON argument (never as argv flags), so a request can
    never inject a subcommand, flag, or path.
    """

    def __init__(self, connection: WasmConnection) -> None:
        self.connection = connection

    def _binary(self) -> str:
        resolved = (
            self.connection.gang_binary
            if self.connection.gang_binary.startswith("/")
            else shutil.which(self.connection.gang_binary)
        )
        if not resolved:
            raise WasmError(
                f"gang binary {self.connection.gang_binary!r} not found",
                kind=AdapterErrorKind.CONFIGURATION,
            )
        return resolved

    async def invoke(self, export: str, payload: dict[str, Any]) -> dict[str, Any]:
        if export not in _EXPORTS:
            raise WasmError(f"unknown lifecycle export {export!r}")
        binary = self._binary()
        argv = [
            binary,
            "--data-dir",
            str(self.connection.data_dir),
            "--format",
            "json",
            "run",
            self.connection.robot,
            self.connection.capability,
            "--export",
            export,
            json.dumps(payload, separators=(",", ":"), default=str),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.connection.invoke_timeout_seconds
            )
        except FileNotFoundError as error:
            raise WasmError(
                "gang binary is unavailable", kind=AdapterErrorKind.CONFIGURATION
            ) from error
        except TimeoutError as error:
            # A timeout after a possibly-started mutation is an unknown outcome.
            raise AdapterError(
                f"gang invoke of {export} timed out",
                kind=AdapterErrorKind.TIMEOUT,
                retry=RetryDisposition.RECONCILE_FIRST,
                reconciliation_required=True,
            ) from error
        if process.returncode != 0:
            # Never surface gang stderr — it may carry peer IDs, paths, addresses.
            raise WasmError(
                f"gang invoke of {export} failed (exit {process.returncode})",
                kind=AdapterErrorKind.UNAVAILABLE,
            )
        if len(stdout) > _MAX_RESULT_BYTES:
            raise WasmError(f"gang {export} result exceeds the size limit")
        try:
            document = json.loads(stdout)
        except (ValueError, json.JSONDecodeError) as error:
            raise WasmError(f"gang {export} returned invalid JSON") from error
        if not isinstance(document, dict):
            raise WasmError(f"gang {export} result envelope is invalid")
        return document

    async def describe(self) -> WasmDescription:
        """Fetch the component's self-description (manifest + contributed names).

        The exact-digest lock plus the Ganglion trust store guarantee this comes
        from the pinned signed artifact — the wasm analogue of the sidecar
        handshake."""
        return WasmDescription.model_validate(await self.invoke("describe", {}))


def _request_payload(action: ActionDefinition, request: ActionRequest) -> dict[str, Any]:
    return {
        "action": action.model_dump(mode="json"),
        "request": request.model_dump(mode="json"),
    }


class GanglionAdapter:
    """A Deckhand adapter backed by a signed WASM component (the ``wasm`` tier).

    Implements the exact ``Adapter`` protocol the in-process and sidecar tiers do,
    and passes the identical frozen conformance suite. Mutation transport loss maps
    to ``UnknownOutcome`` so worker reconciliation is unchanged.
    """

    def __init__(self, adapter_id: str, client: GanglionClient) -> None:
        self.adapter_id = adapter_id
        self.client = client

    async def health(self) -> AdapterHealth:
        return AdapterHealth.model_validate(await self.client.invoke("health", {}))

    async def plan(self, action: ActionDefinition, request: ActionRequest) -> AdapterPlan:
        raw = await self.client.invoke("plan", _request_payload(action, request))
        return AdapterPlan.model_validate(raw)

    async def execute(self, action: ActionDefinition, request: ActionRequest) -> AdapterExecution:
        try:
            raw = await self.client.invoke("execute", _request_payload(action, request))
        except WasmError as error:
            self._raise_unknown_for_mutation(action, error)
            raise
        return AdapterExecution.model_validate(raw)

    async def observe(self, action: ActionDefinition, request: ActionRequest) -> AdapterObservation:
        try:
            raw = await self.client.invoke("observe", _request_payload(action, request))
        except WasmError as error:
            self._raise_unknown_for_mutation(action, error)
            raise
        return AdapterObservation.model_validate(raw)

    async def verify(
        self,
        action: ActionDefinition,
        request: ActionRequest,
        execution: AdapterExecution,
        observation: AdapterObservation,
    ) -> AdapterVerification:
        payload = {
            **_request_payload(action, request),
            "execution": execution.model_dump(mode="json"),
            "observation": observation.model_dump(mode="json"),
        }
        raw = await self.client.invoke("verify", payload)
        return AdapterVerification.model_validate(raw)

    async def cancel(
        self,
        action: ActionDefinition,
        request: ActionRequest,
        execution: AdapterExecution | None,
    ) -> AdapterCancellation:
        payload = {
            **_request_payload(action, request),
            "execution": execution.model_dump(mode="json") if execution else None,
        }
        raw = await self.client.invoke("cancel", payload)
        return AdapterCancellation.model_validate(raw)

    @staticmethod
    def _raise_unknown_for_mutation(action: ActionDefinition, error: WasmError) -> None:
        if action.mutation:
            raise UnknownOutcome("wasm transport failed during mutation") from error


class GanglionStatusProvider:
    """A status provider backed by a WASM component's ``observe`` export."""

    def __init__(self, provider_id: str, client: GanglionClient) -> None:
        self.provider_id = provider_id
        self.client = client

    async def observe(self) -> StatusValue:
        raw = await self.client.invoke("observe", {"provider": self.provider_id})
        return StatusValue.model_validate(raw)
