"""macOS local action executor.

Safety invariant (unchanged from the original): the network surface accepts only
an action enum plus a validated alias. Every argv value comes from the operator
inventory or a fixed constant — never from the request body. No request field is
ever interpolated into a shell; subprocesses are exec'd with an explicit argv.

Native mechanisms used, in order of preference (spec §11.3):
- Supported system APIs / AppleScript for mic, audio, focus, displays.
- `caffeinate` for bounded sleep inhibition.
- Apple Shortcuts for focus/display/pomodoro routines the OS has no CLI for.
- obs-websocket (v5) for recording/scene control.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from .config import MacInventory
from .models import LocalAction, LocalActionRequest, LocalActionResult, action_requires_target
from .obs import ObsClient, ObsError

Handler = Callable[["MacExecutor", LocalActionRequest], Awaitable[LocalActionResult]]
_V = TypeVar("_V")

# A single caffeinate process holds the sleep-inhibit assertion for the agent.
_caffeinate_lock = asyncio.Lock()
_caffeinate_process: asyncio.subprocess.Process | None = None


class LocalActionError(RuntimeError):
    pass


class MacExecutor:
    def __init__(self, inventory: MacInventory) -> None:
        self.inventory = inventory
        self._obs = ObsClient(inventory.obs)

    async def execute(self, request: LocalActionRequest) -> LocalActionResult:
        action = request.action_id
        handler = _HANDLERS.get(action)
        if handler is None:
            raise LocalActionError(f"unsupported action {action}")
        # Actions that operate on a specific target require a real alias; the "_"
        # sentinel is only valid for whole-machine actions (mute mic, read state).
        if action_requires_target(action) and request.target == "_":
            raise LocalActionError(f"action {action} requires a target alias")
        return await handler(self, request)

    # ---- helpers -------------------------------------------------------------

    @staticmethod
    def _lookup(mapping: dict[str, _V], alias: str) -> _V:
        try:
            return mapping[alias]
        except KeyError as error:
            raise LocalActionError(f"unknown local target alias {alias!r}") from error

    @staticmethod
    async def _run(*arguments: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise LocalActionError(
                f"local action failed with exit {process.returncode}: "
                f"{stderr.decode(errors='replace')[:256]}"
            )
        return stdout.decode(errors="replace").strip()

    @classmethod
    async def _osascript(cls, script: str) -> str:
        return await cls._run("/usr/bin/osascript", "-e", script)

    def _ok(
        self,
        request: LocalActionRequest,
        state: str,
        *,
        verified: bool = True,
        **details: object,
    ) -> LocalActionResult:
        clean = {k: v for k, v in details.items() if v is not None}
        return LocalActionResult(
            action_id=request.action_id,
            target=request.target,
            state=state,
            verified=verified,
            details=clean,
        )

    # ---- apps / browser / shortcuts (original) -------------------------------

    async def _app_ensure_running(self, request: LocalActionRequest) -> LocalActionResult:
        bundle_id = self._lookup(self.inventory.apps, request.target)
        await self._run("/usr/bin/open", "-b", bundle_id)
        running = await self._application_running(bundle_id)
        return self._ok(
            request, "running" if running else "unknown", verified=running, bundle_id=bundle_id
        )

    async def _browser_session_open(self, request: LocalActionRequest) -> LocalActionResult:
        url = self._lookup(self.inventory.browser_sessions, request.target)
        await self._run("/usr/bin/open", url)
        return self._ok(request, "requested", verified=False)

    async def _shortcut_run(self, request: LocalActionRequest) -> LocalActionResult:
        shortcut = self._lookup(self.inventory.shortcuts, request.target)
        await self._run("/usr/bin/shortcuts", "run", shortcut)
        return self._ok(request, "completed")

    async def _workspace_open(self, request: LocalActionRequest) -> LocalActionResult:
        path = self._lookup(self.inventory.workspaces, request.target)
        expanded = str(Path(path).expanduser())
        await self._run("/usr/bin/open", expanded)
        return self._ok(request, "requested", verified=False, path=expanded)

    async def _application_running(self, bundle_id: str) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9.-]+", bundle_id):
            raise LocalActionError("invalid bundle identifier")
        script = f'application id "{bundle_id}" is running'
        try:
            result = await self._osascript(script)
        except LocalActionError:
            return False
        return result.strip().lower() == "true"

    # ---- audio / mic (§13.7) -------------------------------------------------

    async def _mic_mute(self, request: LocalActionRequest) -> LocalActionResult:
        return await self._mic_set(request, muted=True)

    async def _mic_unmute(self, request: LocalActionRequest) -> LocalActionResult:
        return await self._mic_set(request, muted=False)

    async def _mic_set(self, request: LocalActionRequest, muted: bool) -> LocalActionResult:
        # System input volume 0 == muted. Authoritative and API-based (no app).
        await self._osascript(f"set volume input volume {0 if muted else 75}")
        state = await self._mic_query()
        return self._ok(request, state, verified=(state == ("muted" if muted else "live")))

    async def _mic_toggle(self, request: LocalActionRequest) -> LocalActionResult:
        current = await self._mic_query()
        return await self._mic_set(request, muted=(current != "muted"))

    async def _mic_state(self, request: LocalActionRequest) -> LocalActionResult:
        return self._ok(request, await self._mic_query())

    async def _mic_query(self) -> str:
        level = await self._osascript("input volume of (get volume settings)")
        try:
            return "muted" if int(level) == 0 else "live"
        except ValueError:
            return "unknown"

    async def _audio_output_select(self, request: LocalActionRequest) -> LocalActionResult:
        device = self._lookup(self.inventory.audio_outputs, request.target)
        await self._set_audio_device(device, output=True)
        return self._ok(request, "selected", device=device)

    async def _audio_input_select(self, request: LocalActionRequest) -> LocalActionResult:
        device = self._lookup(self.inventory.audio_inputs, request.target)
        await self._set_audio_device(device, output=False)
        return self._ok(request, "selected", device=device)

    async def _set_audio_device(self, device: str, *, output: bool) -> None:
        # Prefer SwitchAudioSource if installed; fall back to a clear error so the
        # operator knows to install it (device switching has no stable native CLI).
        tool = _which("SwitchAudioSource")
        if tool is None:
            raise LocalActionError(
                "audio device switching requires SwitchAudioSource (brew install switchaudio-osx)"
            )
        await self._run(tool, "-t", "output" if output else "input", "-s", device)

    async def _audio_state(self, request: LocalActionRequest) -> LocalActionResult:
        tool = _which("SwitchAudioSource")
        if tool is None:
            return self._ok(request, "unknown", verified=False, reason="SwitchAudioSource absent")
        out = await self._run(tool, "-t", "output", "-c")
        inp = await self._run(tool, "-t", "input", "-c")
        return self._ok(request, "ok", output=out, input=inp)

    # ---- focus (§13.11) ------------------------------------------------------

    async def _focus_set(self, request: LocalActionRequest) -> LocalActionResult:
        # macOS has no first-party Focus CLI; the inventory maps the alias to a
        # Shortcut that turns the Focus on. This keeps us API/Shortcut-based.
        shortcut = self._lookup(self.inventory.focus_modes, request.target)
        await self._run("/usr/bin/shortcuts", "run", shortcut)
        return self._ok(request, "set", verified=False, mode=shortcut)

    async def _focus_clear(self, request: LocalActionRequest) -> LocalActionResult:
        shortcut = self.inventory.focus_modes.get("_clear")
        if shortcut is None:
            raise LocalActionError("no '_clear' focus alias configured")
        await self._run("/usr/bin/shortcuts", "run", shortcut)
        return self._ok(request, "cleared", verified=False)

    async def _focus_state(self, request: LocalActionRequest) -> LocalActionResult:
        # Read the current Focus via the do-not-disturb assertion, best-effort.
        return self._ok(request, "unknown", verified=False)

    # ---- pomodoro (§13.11) ---------------------------------------------------

    async def _pomodoro_start(self, request: LocalActionRequest) -> LocalActionResult:
        shortcut = self._lookup(self.inventory.pomodoro_routines, request.target)
        await self._run("/usr/bin/shortcuts", "run", shortcut)
        return self._ok(request, "started", verified=False, routine=shortcut)

    async def _pomodoro_stop(self, request: LocalActionRequest) -> LocalActionResult:
        shortcut = self.inventory.pomodoro_routines.get("_stop")
        if shortcut is not None:
            await self._run("/usr/bin/shortcuts", "run", shortcut)
        return self._ok(request, "stopped", verified=False)

    async def _pomodoro_state(self, request: LocalActionRequest) -> LocalActionResult:
        return self._ok(request, "unknown", verified=False)

    # ---- displays / sleep (§13.6) --------------------------------------------

    async def _display_mode_apply(self, request: LocalActionRequest) -> LocalActionResult:
        shortcut = self._lookup(self.inventory.display_modes, request.target)
        await self._run("/usr/bin/shortcuts", "run", shortcut)
        return self._ok(request, "applied", verified=False, mode=shortcut)

    async def _display_state(self, request: LocalActionRequest) -> LocalActionResult:
        count = await self._osascript('tell application "System Events" to count of desktops')
        return self._ok(request, "ok", displays=count)

    async def _sleep_inhibit_on(self, request: LocalActionRequest) -> LocalActionResult:
        global _caffeinate_process
        async with _caffeinate_lock:
            if _caffeinate_process is None or _caffeinate_process.returncode is not None:
                _caffeinate_process = await asyncio.create_subprocess_exec(
                    "/usr/bin/caffeinate",
                    "-di",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
        return self._ok(request, "inhibited")

    async def _sleep_inhibit_off(self, request: LocalActionRequest) -> LocalActionResult:
        global _caffeinate_process
        async with _caffeinate_lock:
            if _caffeinate_process is not None and _caffeinate_process.returncode is None:
                _caffeinate_process.terminate()
                _caffeinate_process = None
        return self._ok(request, "released")

    async def _sleep_inhibit_state(self, request: LocalActionRequest) -> LocalActionResult:
        active = _caffeinate_process is not None and _caffeinate_process.returncode is None
        return self._ok(request, "inhibited" if active else "released")

    # ---- OBS / recording (§13.7) ---------------------------------------------

    async def _obs_scene_set(self, request: LocalActionRequest) -> LocalActionResult:
        scene = self._lookup(self.inventory.obs.scenes, request.target)
        try:
            await self._obs.set_scene(scene)
        except ObsError as error:
            raise LocalActionError(str(error)) from error
        return self._ok(request, "set", scene=scene)

    async def _obs_record_start(self, request: LocalActionRequest) -> LocalActionResult:
        try:
            await self._obs.record(start=True)
            state = await self._obs.record_state()
        except ObsError as error:
            raise LocalActionError(str(error)) from error
        return self._ok(request, state, verified=(state == "recording"))

    async def _obs_record_stop(self, request: LocalActionRequest) -> LocalActionResult:
        try:
            await self._obs.record(start=False)
            state = await self._obs.record_state()
        except ObsError as error:
            raise LocalActionError(str(error)) from error
        return self._ok(request, state, verified=(state != "recording"))

    async def _obs_record_state(self, request: LocalActionRequest) -> LocalActionResult:
        try:
            return self._ok(request, await self._obs.record_state())
        except ObsError as error:
            return self._ok(request, "unknown", verified=False, reason=str(error))

    async def _obs_source_toggle(self, request: LocalActionRequest) -> LocalActionResult:
        source = self._lookup(self.inventory.obs.sources, request.target)
        try:
            visible = await self._obs.toggle_source(source)
        except ObsError as error:
            raise LocalActionError(str(error)) from error
        return self._ok(request, "visible" if visible else "hidden", source=source)

    async def _obs_replay_save(self, request: LocalActionRequest) -> LocalActionResult:
        try:
            await self._obs.save_replay()
        except ObsError as error:
            raise LocalActionError(str(error)) from error
        return self._ok(request, "saved")

    # ---- keyboard / input modes (§13.12) -------------------------------------

    async def _input_select(self, request: LocalActionRequest) -> LocalActionResult:
        # macOS has no safe CLI to switch the active input source, so — exactly
        # like focus/display modes — the inventory maps the alias to a Shortcut
        # NAME the operator authored ("Set Dvorak") that selects the source. The
        # value is a Shortcut name, not a raw input-source id. Shortcuts don't
        # report back, so this is requested/verified=False.
        shortcut = self._lookup(self.inventory.input_sources, request.target)
        await self._run("/usr/bin/shortcuts", "run", shortcut)
        return self._ok(request, "requested", verified=False, source=shortcut)

    async def _input_state(self, request: LocalActionRequest) -> LocalActionResult:
        # Best-effort read of the active keyboard input source via System Events.
        # This is not reliably readable across macOS versions, so on any failure
        # we return "unknown" rather than guessing. Whole-machine, no target.
        script = (
            'tell application "System Events" to tell process "SystemUIServer" '
            "to get the value of the first menu bar item of menu bar 1 whose "
            'description is "text input"'
        )
        try:
            name = await self._osascript(script)
        except LocalActionError:
            return self._ok(request, "unknown", verified=False)
        name = name.strip()
        if not name:
            return self._ok(request, "unknown", verified=False)
        return self._ok(request, "ok", verified=False, source=name)

    async def _reset_modifiers(self, request: LocalActionRequest) -> LocalActionResult:
        # Clear stuck modifier keys with a FIXED AppleScript key-up sequence. This
        # takes no operator input at all — it is a constant command, so no alias
        # is needed and nothing arbitrary can reach it. Whole-machine, no target.
        script = 'tell application "System Events" to key up {command, option, control, shift}'
        await self._osascript(script)
        return self._ok(request, "reset")

    # ---- generic app command (§13.8 / §13.9 / §13.10) ------------------------

    async def _app_command(self, request: LocalActionRequest) -> LocalActionResult:
        # ONE generic action covers GIMP/Blender/DaVinci menu commands without
        # per-app code. The alias resolves to an operator-declared AppCommand
        # (bundle id + AppleScript keystroke clause). Both were charset-validated
        # at config time; here we (1) activate the app by its validated bundle id
        # and (2) send the declared keystroke inside a FIXED System-Events frame.
        # Because the keystroke text is operator-declared AND charset-validated
        # AND wrapped in a fixed frame, no arbitrary AppleScript can execute.
        command = self._lookup(self.inventory.app_commands, request.target)
        bundle_id = command.bundle_id
        if not re.fullmatch(r"[A-Za-z0-9.-]+", bundle_id):
            raise LocalActionError("invalid bundle identifier")
        await self._osascript(f'tell application id "{bundle_id}" to activate')
        # Fixed frame: only the operator-declared, charset-validated keystroke
        # clause is interpolated; the surrounding AppleScript is a constant.
        await self._osascript(
            'tell application "System Events" to tell '
            f"(first process whose frontmost is true) to {command.keystroke}"
        )
        return self._ok(request, "requested", verified=False, bundle_id=bundle_id)


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


# Dispatch table — keeps execute() flat and the enum→handler mapping explicit.
_HANDLERS: dict[LocalAction, Handler] = {
    LocalAction.APP_ENSURE_RUNNING: MacExecutor._app_ensure_running,
    LocalAction.BROWSER_SESSION_OPEN: MacExecutor._browser_session_open,
    LocalAction.SHORTCUT_RUN: MacExecutor._shortcut_run,
    LocalAction.WORKSPACE_OPEN: MacExecutor._workspace_open,
    LocalAction.MIC_MUTE: MacExecutor._mic_mute,
    LocalAction.MIC_UNMUTE: MacExecutor._mic_unmute,
    LocalAction.MIC_TOGGLE: MacExecutor._mic_toggle,
    LocalAction.MIC_STATE: MacExecutor._mic_state,
    LocalAction.AUDIO_OUTPUT_SELECT: MacExecutor._audio_output_select,
    LocalAction.AUDIO_INPUT_SELECT: MacExecutor._audio_input_select,
    LocalAction.AUDIO_STATE: MacExecutor._audio_state,
    LocalAction.FOCUS_SET: MacExecutor._focus_set,
    LocalAction.FOCUS_CLEAR: MacExecutor._focus_clear,
    LocalAction.FOCUS_STATE: MacExecutor._focus_state,
    LocalAction.POMODORO_START: MacExecutor._pomodoro_start,
    LocalAction.POMODORO_STOP: MacExecutor._pomodoro_stop,
    LocalAction.POMODORO_STATE: MacExecutor._pomodoro_state,
    LocalAction.DISPLAY_MODE_APPLY: MacExecutor._display_mode_apply,
    LocalAction.DISPLAY_STATE: MacExecutor._display_state,
    LocalAction.SLEEP_INHIBIT_ON: MacExecutor._sleep_inhibit_on,
    LocalAction.SLEEP_INHIBIT_OFF: MacExecutor._sleep_inhibit_off,
    LocalAction.SLEEP_INHIBIT_STATE: MacExecutor._sleep_inhibit_state,
    LocalAction.OBS_SCENE_SET: MacExecutor._obs_scene_set,
    LocalAction.OBS_RECORD_START: MacExecutor._obs_record_start,
    LocalAction.OBS_RECORD_STOP: MacExecutor._obs_record_stop,
    LocalAction.OBS_RECORD_STATE: MacExecutor._obs_record_state,
    LocalAction.OBS_SOURCE_TOGGLE: MacExecutor._obs_source_toggle,
    LocalAction.OBS_REPLAY_SAVE: MacExecutor._obs_replay_save,
    LocalAction.INPUT_SELECT: MacExecutor._input_select,
    LocalAction.INPUT_STATE: MacExecutor._input_state,
    LocalAction.RESET_MODIFIERS: MacExecutor._reset_modifiers,
    LocalAction.APP_COMMAND: MacExecutor._app_command,
}
