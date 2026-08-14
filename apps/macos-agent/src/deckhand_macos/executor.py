import asyncio
import re

from .config import MacInventory
from .models import LocalAction, LocalActionRequest, LocalActionResult


class LocalActionError(RuntimeError):
    pass


class MacExecutor:
    def __init__(self, inventory: MacInventory) -> None:
        self.inventory = inventory

    async def execute(self, request: LocalActionRequest) -> LocalActionResult:
        if request.action_id == LocalAction.APP_ENSURE_RUNNING:
            bundle_id = self._lookup(self.inventory.apps, request.target)
            await self._run("/usr/bin/open", "-b", bundle_id)
            running = await self._application_running(bundle_id)
            return LocalActionResult(
                action_id=request.action_id,
                target=request.target,
                state="running" if running else "unknown",
                verified=running,
                details={"bundle_id": bundle_id},
            )
        if request.action_id == LocalAction.BROWSER_SESSION_OPEN:
            url = self._lookup(self.inventory.browser_sessions, request.target)
            await self._run("/usr/bin/open", url)
            return LocalActionResult(
                action_id=request.action_id,
                target=request.target,
                state="requested",
                verified=False,
            )
        shortcut = self._lookup(self.inventory.shortcuts, request.target)
        await self._run("/usr/bin/shortcuts", "run", shortcut)
        return LocalActionResult(
            action_id=request.action_id,
            target=request.target,
            state="completed",
            verified=True,
        )

    @staticmethod
    def _lookup(mapping: dict[str, str], alias: str) -> str:
        try:
            return mapping[alias]
        except KeyError as error:
            raise LocalActionError(f"unknown local target alias {alias!r}") from error

    @staticmethod
    async def _run(*arguments: str) -> None:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise LocalActionError(
                f"local action failed with exit {process.returncode}: "
                f"{stderr.decode(errors='replace')[:256]}"
            )

    async def _application_running(self, bundle_id: str) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9.-]+", bundle_id):
            raise LocalActionError("invalid bundle identifier")
        script = f'application id "{bundle_id}" is running'
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/osascript",
            "-e",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        return process.returncode == 0 and stdout.decode().strip().lower() == "true"
