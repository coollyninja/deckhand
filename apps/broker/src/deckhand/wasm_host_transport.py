"""Peer-authenticated Unix-socket host transport for ``deckhand-wasm-host``.

This is the length-prefixed JSON protocol, ``SO_PEERCRED`` peer authentication,
and signed-artifact/digest/trust verification that the out-of-process WASM host
(``deckhand.wasm_host_main`` and the broker's ``_wasm_out_of_process`` path)
speaks. The broker reaches ``deckhand-wasm-host`` with ``WasmHostClient`` /
``WasmHostAdapter`` / ``WasmHostStatusProvider``; the host serves them with
``WasmHostServer``. Lifecycle results are parsed into the existing ``Adapter*``
and ``StatusValue`` models, and transport loss during a mutation is reduced to
``UnknownOutcome`` so worker reconciliation is unchanged.

Lineage: this transport originated as ADR-0004's Unix-socket sidecar protocol.
The sidecar plugin isolation tier was removed (ADR-0005); its peer-authenticated
socket transport is retained here as the wasm host's transport.
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import hashlib
import json
import math
import os
import socket
import stat
import struct
import sys
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, ValidationError, field_validator

from .adapters import (
    Adapter,
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
from .plugin_api import PLUGIN_API_VERSION, DeckhandPlugin, PluginContext, PluginManifest
from .status import StatusProvider

HOST_PROTOCOL_VERSION = 1
DEFAULT_MAX_FRAME_BYTES = 1_048_576
MAX_PUBLIC_DEPTH = 8
MAX_PUBLIC_ITEMS = 256
MAX_PUBLIC_STRING = 8192
SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
)

T = TypeVar("T", bound=StrictModel)


class HostProtocolError(RuntimeError):
    pass


class HostTransportError(AdapterError):
    pass


class HostOperation(StrEnum):
    HANDSHAKE = "handshake"
    HEALTH = "health"
    PLAN = "plan"
    EXECUTE = "execute"
    OBSERVE = "observe"
    VERIFY = "verify"
    CANCEL = "cancel"
    STATUS_OBSERVE = "status.observe"


class WasmHostConnection(StrictModel):
    socket_path: Path
    socket_root: Path = Path("/run/deckhand/plugins")
    expected_uid: int = Field(ge=0)
    artifact_path: Path
    signature_path: Path
    public_key_path: Path
    trust_root: Path = Path("/etc/deckhand/trust")
    trust_owner_uid: int = Field(default=0, ge=0)
    artifact_owner_uid: int = Field(default=0, ge=0)
    connect_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    max_frame_bytes: int = Field(default=DEFAULT_MAX_FRAME_BYTES, ge=4096, le=4_194_304)
    max_artifact_bytes: int = Field(default=134_217_728, ge=1024, le=1_073_741_824)

    @field_validator(
        "socket_path",
        "socket_root",
        "artifact_path",
        "signature_path",
        "public_key_path",
        "trust_root",
    )
    @classmethod
    def require_absolute_paths(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("host transport paths must be absolute")
        return value


class HostRequest(StrictModel):
    protocol_version: int = HOST_PROTOCOL_VERSION
    request_id: UUID = Field(default_factory=uuid4)
    plugin_id: str = Field(pattern=r"^dh-[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    operation: HostOperation
    payload: dict[str, Any] = Field(default_factory=dict)


class HostFailure(StrictModel):
    kind: AdapterErrorKind
    retry: RetryDisposition = RetryDisposition.NEVER
    reconciliation_required: bool = False


class HostResponse(StrictModel):
    protocol_version: int = HOST_PROTOCOL_VERSION
    request_id: UUID
    ok: bool
    result: dict[str, Any] | None = None
    error: HostFailure | None = None


class HostHandshake(StrictModel):
    protocol_version: int = HOST_PROTOCOL_VERSION
    plugin_id: str = Field(pattern=r"^dh-[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    artifact_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    manifest: PluginManifest
    actions: list[ActionDefinition] = Field(default_factory=list, max_length=1024)
    status_providers: list[str] = Field(default_factory=list, max_length=1024)

    @field_validator("status_providers")
    @classmethod
    def validate_status_providers(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("host status provider IDs must be unique")
        for name in value:
            if not name or len(name) > 256:
                raise ValueError("host status provider IDs must be between 1 and 256 bytes")
        return value


def artifact_digest(path: Path, *, max_bytes: int) -> str:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise HostProtocolError("host artifact must be a regular non-symlink file")
    if metadata.st_size > max_bytes:
        raise HostProtocolError("host artifact exceeds the configured size limit")
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        while chunk := artifact.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def verify_signed_artifact(connection: WasmHostConnection, expected_digest: str) -> None:
    artifact_path = connection.artifact_path.resolve(strict=True)
    if artifact_path != connection.artifact_path:
        raise HostProtocolError("host artifact path cannot contain symlinks")
    artifact_metadata = artifact_path.lstat()
    if artifact_metadata.st_uid != connection.artifact_owner_uid:
        raise HostProtocolError("host artifact has an unexpected owner")
    if artifact_metadata.st_mode & 0o022:
        raise HostProtocolError("host artifact cannot be group/world writable")
    actual_digest = artifact_digest(
        artifact_path,
        max_bytes=connection.max_artifact_bytes,
    )
    if actual_digest != expected_digest:
        raise HostProtocolError("host artifact digest does not match the plugin lock")

    _validate_trust_path(connection)
    public_key = serialization.load_pem_public_key(connection.public_key_path.read_bytes())
    if not isinstance(public_key, Ed25519PublicKey):
        raise HostProtocolError("host trust key must be an Ed25519 public key")
    try:
        signature = base64.b64decode(
            connection.signature_path.read_bytes().strip(),
            validate=True,
        )
        public_key.verify(signature, expected_digest.encode("ascii"))
    except (InvalidSignature, ValueError) as error:
        raise HostProtocolError("host artifact signature verification failed") from error


def _validate_trust_path(connection: WasmHostConnection) -> None:
    trust_root = connection.trust_root.resolve(strict=True)
    key_path = connection.public_key_path.resolve(strict=True)
    if trust_root != connection.trust_root or key_path != connection.public_key_path:
        raise HostProtocolError("host trust paths cannot contain symlinks")
    try:
        key_path.relative_to(trust_root)
    except ValueError as error:
        raise HostProtocolError("host public key is outside the configured trust root") from error
    for path in (trust_root, key_path):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise HostProtocolError("host trust paths cannot be symlinks")
        if metadata.st_uid != connection.trust_owner_uid:
            raise HostProtocolError("host trust paths have an unexpected owner")
        if metadata.st_mode & 0o022:
            raise HostProtocolError("host trust paths cannot be group/world writable")
    if not stat.S_ISDIR(trust_root.lstat().st_mode):
        raise HostProtocolError("host trust root must be a directory")
    if not stat.S_ISREG(key_path.lstat().st_mode):
        raise HostProtocolError("host trust key must be a regular file")


def _validate_socket_path(connection: WasmHostConnection) -> None:
    socket_root = connection.socket_root.resolve(strict=True)
    socket_path = connection.socket_path.resolve(strict=True)
    if socket_root != connection.socket_root or socket_path != connection.socket_path:
        raise HostProtocolError("host socket path cannot contain symlinks")
    try:
        socket_path.relative_to(socket_root)
    except ValueError as error:
        raise HostProtocolError("host socket is outside the configured socket root") from error
    current = socket_path.parent
    while True:
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise HostProtocolError("host socket directories must not be symlinks")
        if metadata.st_mode & 0o022:
            raise HostProtocolError("host socket directories cannot be group/world writable")
        if current == socket_root:
            break
        current = current.parent
    metadata = socket_path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISSOCK(metadata.st_mode):
        raise HostProtocolError("host endpoint must be a Unix socket")
    if metadata.st_uid != connection.expected_uid:
        raise HostProtocolError("host socket has an unexpected owner")
    if metadata.st_mode & 0o002:
        raise HostProtocolError("host socket cannot be world writable")


def _peer_uid(peer: Any) -> int:
    if hasattr(socket, "SO_PEERCRED"):
        raw = peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _, uid, _ = struct.unpack("3i", raw)
        return int(uid)
    if sys.platform in {"darwin", "freebsd"}:
        uid = ctypes.c_uint()
        gid = ctypes.c_uint()
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.getpeereid(peer.fileno(), ctypes.byref(uid), ctypes.byref(gid))
        if result != 0:
            raise OSError(ctypes.get_errno(), "getpeereid failed")
        return int(uid.value)
    raise HostProtocolError("Unix peer credential verification is unsupported")


def _encode_frame(model: StrictModel, max_bytes: int) -> bytes:
    payload = model.model_dump_json().encode("utf-8")
    if not payload or len(payload) > max_bytes:
        raise HostProtocolError("host frame exceeds the configured size limit")
    return struct.pack(">I", len(payload)) + payload


async def _read_frame(reader: asyncio.StreamReader, max_bytes: int) -> dict[str, Any]:
    header = await reader.readexactly(4)
    size = struct.unpack(">I", header)[0]
    if size == 0 or size > max_bytes:
        raise HostProtocolError("host frame has an invalid size")
    payload = await reader.readexactly(size)
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise HostProtocolError("host frame must contain a JSON object")
    return value


def _recv_exact(peer: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = peer.recv(remaining)
        if not chunk:
            raise HostProtocolError("host closed before completing a frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_frame_sync(peer: socket.socket, max_bytes: int) -> dict[str, Any]:
    size = struct.unpack(">I", _recv_exact(peer, 4))[0]
    if size == 0 or size > max_bytes:
        raise HostProtocolError("host frame has an invalid size")
    value = json.loads(_recv_exact(peer, size))
    if not isinstance(value, dict):
        raise HostProtocolError("host frame must contain a JSON object")
    return value


def _validate_public_json(value: Any, *, depth: int = 0) -> None:
    if depth > MAX_PUBLIC_DEPTH:
        raise HostProtocolError("host payload exceeds the maximum nesting depth")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HostProtocolError("host payload contains a non-finite number")
        return
    if isinstance(value, str):
        if len(value) > MAX_PUBLIC_STRING:
            raise HostProtocolError("host payload contains an oversized string")
        return
    if isinstance(value, list):
        if len(value) > MAX_PUBLIC_ITEMS:
            raise HostProtocolError("host payload contains too many list items")
        for item in value:
            _validate_public_json(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_PUBLIC_ITEMS:
            raise HostProtocolError("host payload contains too many object fields")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 128:
                raise HostProtocolError("host payload contains an invalid object key")
            lowered = key.lower()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                raise HostProtocolError("host payload contains a prohibited sensitive field")
            _validate_public_json(item, depth=depth + 1)
        return
    raise HostProtocolError("host payload contains an unsupported JSON value")


def _request_dump(request: ActionRequest) -> dict[str, Any]:
    value = request.model_dump(mode="json", exclude={"confirmation_token"})
    _validate_public_json(value)
    return value


class WasmHostClient:
    def __init__(
        self,
        plugin_id: str,
        connection: WasmHostConnection,
        expected_digest: str,
    ) -> None:
        self.plugin_id = plugin_id
        self.connection = connection
        self.expected_digest = expected_digest
        verify_signed_artifact(connection, expected_digest)

    def handshake(self) -> HostHandshake:
        response = self._call_sync(HostOperation.HANDSHAKE, {})
        handshake = HostHandshake.model_validate(response)
        self._validate_handshake(handshake)
        return handshake

    async def call(
        self,
        operation: HostOperation,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _validate_public_json(payload)
        request = HostRequest(
            plugin_id=self.plugin_id,
            operation=operation,
            payload=payload,
        )
        writer: asyncio.StreamWriter | None = None
        try:
            _validate_socket_path(self.connection)
            reader, active_writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.connection.socket_path)),
                timeout=self.connection.connect_timeout_seconds,
            )
            writer = active_writer
            peer = active_writer.get_extra_info("socket")
            if peer is None or _peer_uid(peer) != self.connection.expected_uid:
                raise HostProtocolError("host peer identity does not match configuration")
            active_writer.write(_encode_frame(request, self.connection.max_frame_bytes))
            await active_writer.drain()
            raw = await _read_frame(reader, self.connection.max_frame_bytes)
            try:
                response = HostResponse.model_validate(raw)
            except ValidationError as error:
                # A malformed response frame after a possibly-started mutation must
                # be a transport-level unknown, not a hard failure -- otherwise the
                # mutation path would record FAILED and skip reconciliation.
                raise HostTransportError(
                    "host response failed schema validation",
                    kind=AdapterErrorKind.PROTOCOL,
                ) from error
            return self._unwrap(request, response)
        except AdapterError:
            raise
        except HostProtocolError as error:
            raise HostTransportError(
                "host protocol validation failed",
                kind=AdapterErrorKind.PROTOCOL,
            ) from error
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as error:
            raise HostTransportError(
                "host is unavailable",
                kind=AdapterErrorKind.UNAVAILABLE,
                retry=RetryDisposition.SAFE,
            ) from error
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()

    def _call_sync(self, operation: HostOperation, payload: dict[str, Any]) -> dict[str, Any]:
        request = HostRequest(
            plugin_id=self.plugin_id,
            operation=operation,
            payload=payload,
        )
        try:
            _validate_socket_path(self.connection)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.settimeout(self.connection.connect_timeout_seconds)
                peer.connect(str(self.connection.socket_path))
                if _peer_uid(peer) != self.connection.expected_uid:
                    raise HostProtocolError("host peer identity does not match configuration")
                peer.sendall(_encode_frame(request, self.connection.max_frame_bytes))
                response = HostResponse.model_validate(
                    _read_frame_sync(peer, self.connection.max_frame_bytes)
                )
            return self._unwrap(request, response)
        except (OSError, TimeoutError) as error:
            raise HostProtocolError("host handshake failed") from error

    def _unwrap(self, request: HostRequest, response: HostResponse) -> dict[str, Any]:
        if response.protocol_version != HOST_PROTOCOL_VERSION:
            raise HostProtocolError("host response uses an unsupported protocol version")
        if response.request_id != request.request_id:
            raise HostProtocolError("host response request ID does not match")
        if response.ok:
            if response.error is not None or response.result is None:
                raise HostProtocolError("successful host response has an invalid shape")
            if request.operation != HostOperation.HANDSHAKE:
                _validate_public_json(response.result)
            return response.result
        if response.error is None or response.result is not None:
            raise HostProtocolError("failed host response has an invalid shape")
        raise AdapterError(
            "host plugin operation failed",
            kind=response.error.kind,
            retry=response.error.retry,
            reconciliation_required=response.error.reconciliation_required,
        )

    def _validate_handshake(self, handshake: HostHandshake) -> None:
        if handshake.protocol_version != HOST_PROTOCOL_VERSION:
            raise HostProtocolError("host uses an unsupported protocol version")
        if handshake.plugin_id != self.plugin_id or handshake.manifest.id != self.plugin_id:
            raise HostProtocolError("host handshake plugin ID does not match")
        if handshake.artifact_digest != self.expected_digest:
            raise HostProtocolError("host is not running the locked artifact")
        action_ids = [action.id for action in handshake.actions]
        if len(action_ids) != len(set(action_ids)) or set(action_ids) != set(
            handshake.manifest.actions
        ):
            raise HostProtocolError("host actions do not match the manifest")
        for action in handshake.actions:
            if action.plugin != self.plugin_id or action.adapter not in handshake.manifest.adapters:
                raise HostProtocolError("host action ownership does not match the manifest")
            _validate_public_json(action.parameter_schema)


class WasmHostAdapter:
    def __init__(self, adapter_id: str, client: WasmHostClient) -> None:
        self.adapter_id = adapter_id
        self.client = client

    async def _model(
        self,
        operation: HostOperation,
        payload: dict[str, Any],
        model: type[T],
    ) -> T:
        raw = await self.client.call(operation, payload)
        try:
            return model.model_validate(raw)
        except ValidationError as error:
            # A well-framed response whose result body does not match the expected
            # lifecycle model is a protocol-level transport error, so the mutation
            # wrappers classify it as UNKNOWN_OUTCOME rather than letting a raw
            # ValidationError escape untyped.
            raise HostTransportError(
                f"host {operation.value} result failed schema validation",
                kind=AdapterErrorKind.PROTOCOL,
            ) from error

    async def health(self) -> AdapterHealth:
        return await self._model(
            HostOperation.HEALTH,
            {"adapter": self.adapter_id},
            AdapterHealth,
        )

    async def plan(self, action: ActionDefinition, request: ActionRequest) -> AdapterPlan:
        return await self._model(
            HostOperation.PLAN,
            self._action_payload(action, request),
            AdapterPlan,
        )

    async def execute(self, action: ActionDefinition, request: ActionRequest) -> AdapterExecution:
        try:
            return await self._model(
                HostOperation.EXECUTE,
                self._action_payload(action, request),
                AdapterExecution,
            )
        except HostTransportError as error:
            self._raise_unknown_for_mutation(action, error)
            raise

    async def observe(self, action: ActionDefinition, request: ActionRequest) -> AdapterObservation:
        try:
            return await self._model(
                HostOperation.OBSERVE,
                self._action_payload(action, request),
                AdapterObservation,
            )
        except HostTransportError as error:
            self._raise_unknown_for_mutation(action, error)
            raise

    async def verify(
        self,
        action: ActionDefinition,
        request: ActionRequest,
        execution: AdapterExecution,
        observation: AdapterObservation,
    ) -> AdapterVerification:
        payload = self._action_payload(action, request)
        payload.update(
            {
                "execution": execution.model_dump(mode="json"),
                "observation": observation.model_dump(mode="json"),
            }
        )
        try:
            return await self._model(HostOperation.VERIFY, payload, AdapterVerification)
        except HostTransportError as error:
            self._raise_unknown_for_mutation(action, error)
            raise

    async def cancel(
        self,
        action: ActionDefinition,
        request: ActionRequest,
        execution: AdapterExecution | None,
    ) -> AdapterCancellation:
        payload = self._action_payload(action, request)
        payload["execution"] = execution.model_dump(mode="json") if execution else None
        try:
            return await self._model(HostOperation.CANCEL, payload, AdapterCancellation)
        except HostTransportError as error:
            self._raise_unknown_for_mutation(action, error)
            raise

    def _action_payload(
        self,
        action: ActionDefinition,
        request: ActionRequest,
    ) -> dict[str, Any]:
        return {
            "adapter": self.adapter_id,
            "action": action.model_dump(mode="json"),
            "request": _request_dump(request),
        }

    @staticmethod
    def _raise_unknown_for_mutation(
        action: ActionDefinition,
        error: HostTransportError,
    ) -> None:
        if action.mutation:
            raise UnknownOutcome("host transport failed during mutation") from error


class WasmHostStatusProvider:
    def __init__(self, provider_id: str, client: WasmHostClient) -> None:
        self.provider_id = provider_id
        self.client = client

    async def observe(self) -> StatusValue:
        return StatusValue.model_validate(
            await self.client.call(
                HostOperation.STATUS_OBSERVE,
                {"provider": self.provider_id},
            )
        )


class WasmHostServer:
    def __init__(
        self,
        *,
        plugin: DeckhandPlugin,
        config: Mapping[str, Any],
        artifact_path: Path,
        socket_path: Path,
        broker_uid: int,
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
        max_artifact_bytes: int = 134_217_728,
    ) -> None:
        self.plugin = plugin
        self.manifest = PluginManifest.model_validate(plugin.manifest)
        if self.manifest.api_version != PLUGIN_API_VERSION:
            raise HostProtocolError("plugin requires an unsupported API version")
        self.contribution = plugin.build(PluginContext(config=config))
        self.artifact_digest = artifact_digest(artifact_path, max_bytes=max_artifact_bytes)
        self.socket_path = socket_path
        self.broker_uid = broker_uid
        self.max_frame_bytes = max_frame_bytes
        self._server: asyncio.AbstractServer | None = None
        self._actions = {action.id: action for action in self.contribution.actions}
        self._validate_contribution()

    def _validate_contribution(self) -> None:
        if set(self.contribution.adapters) != set(self.manifest.adapters):
            raise HostProtocolError("host adapter contribution differs from manifest")
        if set(self._actions) != set(self.manifest.actions):
            raise HostProtocolError("host action contribution differs from manifest")
        for name, adapter in self.contribution.adapters.items():
            if not isinstance(adapter, Adapter):
                raise HostProtocolError(f"host adapter {name!r} is incomplete")
        for name, provider in self.contribution.status_providers.items():
            if not isinstance(provider, StatusProvider):
                raise HostProtocolError(f"host status provider {name!r} is incomplete")

    async def start(self) -> asyncio.AbstractServer:
        parent = self.socket_path.parent
        if parent.resolve(strict=True) != parent:
            raise HostProtocolError("host socket parent cannot contain symlinks")
        metadata = parent.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise HostProtocolError("host socket parent must be a real directory")
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
            raise HostProtocolError("host socket parent has unsafe ownership or mode")
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise HostProtocolError("host socket path already exists")
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self.socket_path),
        )
        self.socket_path.chmod(0o660)
        return self._server

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            if self.socket_path.exists() and stat.S_ISSOCK(self.socket_path.lstat().st_mode):
                self.socket_path.unlink()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            peer = writer.get_extra_info("socket")
            if peer is None or _peer_uid(peer) != self.broker_uid:
                return
            request = HostRequest.model_validate(await _read_frame(reader, self.max_frame_bytes))
            response = await self._dispatch(request)
            writer.write(_encode_frame(response, self.max_frame_bytes))
            await writer.drain()
        except (HostProtocolError, ValueError, json.JSONDecodeError):
            return
        finally:
            writer.close()
            await writer.wait_closed()

    async def _dispatch(self, request: HostRequest) -> HostResponse:
        if request.protocol_version != HOST_PROTOCOL_VERSION:
            return self._failure(request, AdapterErrorKind.PROTOCOL)
        if request.plugin_id != self.manifest.id:
            return self._failure(request, AdapterErrorKind.AUTHENTICATION)
        try:
            result = await self._invoke(request.operation, request.payload)
            if request.operation != HostOperation.HANDSHAKE:
                _validate_public_json(result)
            return HostResponse(request_id=request.request_id, ok=True, result=result)
        except AdapterError as error:
            return self._failure(
                request,
                error.kind,
                retry=error.retry,
                reconciliation_required=error.reconciliation_required,
            )
        except (HostProtocolError, ValueError, KeyError):
            return self._failure(request, AdapterErrorKind.PROTOCOL)
        except Exception:
            return self._failure(request, AdapterErrorKind.UNEXPECTED)

    async def _invoke(
        self,
        operation: HostOperation,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if operation == HostOperation.HANDSHAKE:
            return HostHandshake(
                plugin_id=self.manifest.id,
                artifact_digest=self.artifact_digest,
                manifest=self.manifest,
                actions=list(self._actions.values()),
                status_providers=sorted(self.contribution.status_providers),
            ).model_dump(mode="json")
        if operation == HostOperation.STATUS_OBSERVE:
            provider = self.contribution.status_providers[str(payload["provider"])]
            return (await provider.observe()).model_dump(mode="json")

        adapter = self.contribution.adapters[str(payload["adapter"])]
        if operation == HostOperation.HEALTH:
            return (await adapter.health()).model_dump(mode="json")
        action = ActionDefinition.model_validate(payload["action"])
        request = ActionRequest.model_validate(payload["request"])
        if self._actions.get(action.id) != action or action.adapter != str(payload["adapter"]):
            raise HostProtocolError("host action does not match its declared contract")
        result: StrictModel
        if operation == HostOperation.PLAN:
            result = await adapter.plan(action, request)
        elif operation == HostOperation.EXECUTE:
            result = await adapter.execute(action, request)
        elif operation == HostOperation.OBSERVE:
            result = await adapter.observe(action, request)
        elif operation == HostOperation.VERIFY:
            result = await adapter.verify(
                action,
                request,
                AdapterExecution.model_validate(payload["execution"]),
                AdapterObservation.model_validate(payload["observation"]),
            )
        elif operation == HostOperation.CANCEL:
            execution = payload.get("execution")
            result = await adapter.cancel(
                action,
                request,
                AdapterExecution.model_validate(execution) if execution is not None else None,
            )
        else:
            raise HostProtocolError("host operation is unsupported")
        return result.model_dump(mode="json")

    @staticmethod
    def _failure(
        request: HostRequest,
        kind: AdapterErrorKind,
        *,
        retry: RetryDisposition = RetryDisposition.NEVER,
        reconciliation_required: bool = False,
    ) -> HostResponse:
        return HostResponse(
            request_id=request.request_id,
            ok=False,
            error=HostFailure(
                kind=kind,
                retry=retry,
                reconciliation_required=reconciliation_required,
            ),
        )
