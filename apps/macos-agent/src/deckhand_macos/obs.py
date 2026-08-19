"""Minimal obs-websocket (v5) client for the recording/OBS action collection.

Only the handful of operations Deckhand exposes are implemented: set scene,
start/stop recording, read record state, toggle a source's visibility, and save
the replay buffer. Auth follows the obs-websocket v5 challenge/response
(SHA-256 of password+salt, then of that+challenge, base64). No third-party
dependency — a tiny hand-rolled client over the standard library websocket via
the `websockets` package which ships with the agent's deps (uvicorn[standard]).
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from .config import ObsSettings


class ObsError(RuntimeError):
    pass


def _auth_response(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class ObsClient:
    def __init__(self, settings: ObsSettings) -> None:
        self.settings = settings

    def _password(self) -> str | None:
        if self.settings.password_file is None:
            return None
        try:
            return Path(self.settings.password_file).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise ObsError(f"OBS password file unavailable: {error}") from error

    async def _request(
        self, request_type: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            import websockets
        except ImportError as error:  # pragma: no cover
            raise ObsError("the 'websockets' package is required for OBS control") from error

        url = f"ws://{self.settings.host}:{self.settings.port}"
        try:
            async with websockets.connect(url, open_timeout=3, close_timeout=3) as ws:
                hello = json.loads(await ws.recv())
                identify: dict[str, Any] = {"op": 1, "d": {"rpcVersion": 1}}
                auth = hello.get("d", {}).get("authentication")
                if auth:
                    password = self._password()
                    if not password:
                        raise ObsError("OBS requires a password but none is configured")
                    identify["d"]["authentication"] = _auth_response(
                        password, auth["salt"], auth["challenge"]
                    )
                await ws.send(json.dumps(identify))
                identified = json.loads(await ws.recv())
                if identified.get("op") != 2:
                    raise ObsError("OBS identify handshake failed")

                await ws.send(
                    json.dumps(
                        {
                            "op": 6,
                            "d": {
                                "requestType": request_type,
                                "requestId": "deckhand",
                                "requestData": data or {},
                            },
                        }
                    )
                )
                while True:
                    message = json.loads(await ws.recv())
                    if message.get("op") == 7 and message["d"].get("requestId") == "deckhand":
                        status = message["d"].get("requestStatus", {})
                        if not status.get("result", False):
                            raise ObsError(
                                f"OBS request {request_type} failed: {status.get('comment')}"
                            )
                        response: dict[str, Any] = message["d"].get("responseData") or {}
                        return response
        except OSError as error:
            raise ObsError(f"OBS is unreachable: {error}") from error

    async def set_scene(self, scene: str) -> None:
        await self._request("SetCurrentProgramScene", {"sceneName": scene})

    async def record(self, *, start: bool) -> None:
        await self._request("StartRecord" if start else "StopRecord")

    async def record_state(self) -> str:
        data = await self._request("GetRecordStatus")
        return "recording" if data.get("outputActive") else "idle"

    async def toggle_source(self, source: str) -> bool:
        scene = (await self._request("GetCurrentProgramScene")).get("currentProgramSceneName")
        items = await self._request("GetSceneItemList", {"sceneName": scene})
        item_id = None
        for item in items.get("sceneItems", []):
            if item.get("sourceName") == source:
                item_id = item.get("sceneItemId")
                enabled = item.get("sceneItemEnabled", True)
                break
        else:
            raise ObsError(f"OBS source {source!r} not found in current scene")
        new_state = not enabled
        await self._request(
            "SetSceneItemEnabled",
            {"sceneName": scene, "sceneItemId": item_id, "sceneItemEnabled": new_state},
        )
        return new_state

    async def save_replay(self) -> None:
        await self._request("SaveReplayBuffer")
