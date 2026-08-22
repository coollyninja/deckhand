"""macOS executor: alias-only safety + correct dispatch, with subprocess faked."""

from typing import Any

import pytest
from deckhand_macos.config import AppCommand, MacInventory, ObsSettings
from deckhand_macos.executor import LocalActionError, MacExecutor
from deckhand_macos.models import LocalAction, LocalActionRequest
from pydantic import ValidationError


def _inventory() -> MacInventory:
    return MacInventory(
        apps={"terminal": "com.apple.Terminal"},
        audio_outputs={"speakers": "MacBook Speakers"},
        audio_inputs={"builtin": "MacBook Microphone"},
        focus_modes={"work": "Set Work Focus", "_clear": "Clear Focus"},
        pomodoro_routines={"deep": "Start Deep Focus", "_stop": "Stop Focus"},
        display_modes={"office": "Office Layout"},
        workspaces={"vault": "~/Documents/Vaults/example"},
        input_sources={"dvorak": "Set Dvorak"},
        app_commands={
            "gimp_export_png": AppCommand(
                bundle_id="org.gimp.gimp",
                keystroke='keystroke "e" using {command down, shift down}',
            )
        },
        obs=ObsSettings(scenes={"main": "Main"}, sources={"cam": "Camera"}),
    )


@pytest.fixture
def executor(monkeypatch: pytest.MonkeyPatch) -> MacExecutor:
    calls: list[tuple[str, ...]] = []

    async def fake_run(*arguments: str) -> str:
        calls.append(arguments)
        # Return plausible values for the query paths.
        if "input volume of" in " ".join(arguments):
            return "0"
        return ""

    ex = MacExecutor(_inventory())
    ex._captured = calls  # type: ignore[attr-defined]
    monkeypatch.setattr(MacExecutor, "_run", staticmethod(fake_run))
    return ex


async def _exec(ex: MacExecutor, action: LocalAction, target: str = "_") -> Any:
    return await ex.execute(LocalActionRequest(action_id=action, target=target))


@pytest.mark.asyncio
async def test_mic_mute_sets_input_volume_zero(executor: MacExecutor) -> None:
    result = await _exec(executor, LocalAction.MIC_MUTE)
    assert result.state == "muted"
    assert any("set volume input volume 0" in " ".join(c) for c in executor._captured)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_targeted_action_rejects_sentinel_target(executor: MacExecutor) -> None:
    # audio_output_select needs a real alias, not the "_" whole-machine sentinel.
    with pytest.raises(LocalActionError, match="requires a target"):
        await _exec(executor, LocalAction.AUDIO_OUTPUT_SELECT, target="_")


@pytest.mark.asyncio
async def test_unknown_alias_is_rejected(executor: MacExecutor) -> None:
    with pytest.raises(LocalActionError, match="unknown local target"):
        await _exec(executor, LocalAction.FOCUS_SET, target="nope")


@pytest.mark.asyncio
async def test_focus_set_runs_the_mapped_shortcut(executor: MacExecutor) -> None:
    await _exec(executor, LocalAction.FOCUS_SET, target="work")
    assert any(
        c[:2] == ("/usr/bin/shortcuts", "run") and "Set Work Focus" in c
        for c in executor._captured  # type: ignore[attr-defined]
    )


@pytest.mark.asyncio
async def test_pomodoro_start_runs_routine(executor: MacExecutor) -> None:
    result = await _exec(executor, LocalAction.POMODORO_START, target="deep")
    assert result.state == "started"


@pytest.mark.asyncio
async def test_display_mode_apply_runs_shortcut(executor: MacExecutor) -> None:
    result = await _exec(executor, LocalAction.DISPLAY_MODE_APPLY, target="office")
    assert result.state == "applied"


@pytest.mark.asyncio
async def test_workspace_open_expands_and_opens(executor: MacExecutor) -> None:
    result = await _exec(executor, LocalAction.WORKSPACE_OPEN, target="vault")
    assert result.details["path"].endswith("/Documents/Vaults/example")


@pytest.mark.asyncio
async def test_sleep_inhibit_state_when_never_started(executor: MacExecutor) -> None:
    result = await _exec(executor, LocalAction.SLEEP_INHIBIT_STATE)
    assert result.state in {"released", "inhibited"}


@pytest.mark.asyncio
async def test_all_actions_have_a_handler() -> None:
    from deckhand_macos.executor import _HANDLERS

    for action in LocalAction:
        assert action in _HANDLERS


@pytest.mark.asyncio
async def test_obs_error_surfaces_as_local_error(
    executor: MacExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    from deckhand_macos.obs import ObsError

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise ObsError("OBS is unreachable")

    monkeypatch.setattr(executor._obs, "set_scene", boom)
    with pytest.raises(LocalActionError, match="unreachable"):
        await _exec(executor, LocalAction.OBS_SCENE_SET, target="main")


# ---- keyboard / input modes (§13.12) -------------------------------------


@pytest.mark.asyncio
async def test_input_select_runs_the_mapped_shortcut(executor: MacExecutor) -> None:
    result = await _exec(executor, LocalAction.INPUT_SELECT, target="dvorak")
    assert result.state == "requested"
    assert result.verified is False
    assert any(
        c[:2] == ("/usr/bin/shortcuts", "run") and "Set Dvorak" in c
        for c in executor._captured  # type: ignore[attr-defined]
    )


@pytest.mark.asyncio
async def test_input_select_unknown_alias_is_rejected(executor: MacExecutor) -> None:
    with pytest.raises(LocalActionError, match="unknown local target"):
        await _exec(executor, LocalAction.INPUT_SELECT, target="nope")


@pytest.mark.asyncio
async def test_input_state_returns_unknown_when_unreadable(
    executor: MacExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_a: Any) -> str:
        raise LocalActionError("no such menu bar item")

    monkeypatch.setattr(MacExecutor, "_run", staticmethod(boom))
    result = await _exec(executor, LocalAction.INPUT_STATE)
    assert result.state == "unknown"
    assert result.verified is False


@pytest.mark.asyncio
async def test_reset_modifiers_runs_fixed_command_without_target(executor: MacExecutor) -> None:
    result = await _exec(executor, LocalAction.RESET_MODIFIERS)
    assert result.state == "reset"
    # Fixed AppleScript key-up sequence, no operator input interpolated.
    assert any(
        "key up {command, option, control, shift}" in " ".join(c)
        for c in executor._captured  # type: ignore[attr-defined]
    )


# ---- generic app command (§13.8 / §13.9 / §13.10) ------------------------


@pytest.mark.asyncio
async def test_app_command_activates_bundle_and_sends_declared_keystroke(
    executor: MacExecutor,
) -> None:
    result = await _exec(executor, LocalAction.APP_COMMAND, target="gimp_export_png")
    assert result.state == "requested"
    assert result.verified is False
    joined = [" ".join(c) for c in executor._captured]  # type: ignore[attr-defined]
    # The app is activated by its declared bundle id.
    assert any('tell application id "org.gimp.gimp" to activate' in c for c in joined)
    # The exact declared keystroke is sent inside the fixed System-Events frame.
    assert any(
        'tell application "System Events" to tell '
        "(first process whose frontmost is true) to "
        'keystroke "e" using {command down, shift down}' in c
        for c in joined
    )


@pytest.mark.asyncio
async def test_app_command_unknown_alias_is_rejected(executor: MacExecutor) -> None:
    with pytest.raises(LocalActionError, match="unknown local target"):
        await _exec(executor, LocalAction.APP_COMMAND, target="nope")


# ---- config-validation safety (Pydantic) ---------------------------------


@pytest.mark.parametrize(
    "keystroke",
    [
        'keystroke "e"; do shell script "rm -rf /"',  # semicolon + shell
        "keystroke `whoami`",  # backtick
        'keystroke "e"\ndo shell script "boom"',  # newline
        'keystroke "e" & return',  # ampersand
        'tell application "Terminal" to do script "boom"',  # smuggled tell
    ],
)
def test_app_command_rejects_dangerous_keystroke(keystroke: str) -> None:
    with pytest.raises(ValidationError):
        AppCommand(bundle_id="org.gimp.gimp", keystroke=keystroke)


def test_app_command_rejects_bad_bundle_id() -> None:
    with pytest.raises(ValidationError):
        AppCommand(
            bundle_id="org.gimp.gimp; rm -rf /",
            keystroke='keystroke "e" using {command down}',
        )


def test_app_command_accepts_declared_keystroke() -> None:
    command = AppCommand(
        bundle_id="org.gimp.gimp",
        keystroke='keystroke "e" using {command down, shift down}',
    )
    assert command.bundle_id == "org.gimp.gimp"


def test_input_sources_and_app_commands_validate() -> None:
    inventory = MacInventory(
        input_sources={"dvorak": "Set Dvorak"},
        app_commands={
            "blender_render": AppCommand(
                bundle_id="org.blenderfoundation.blender",
                keystroke="key code 96",
            )
        },
    )
    assert inventory.input_sources["dvorak"] == "Set Dvorak"
    assert inventory.app_commands["blender_render"].bundle_id == "org.blenderfoundation.blender"


def test_input_sources_rejects_bad_shortcut_name() -> None:
    with pytest.raises(ValidationError):
        MacInventory(input_sources={"dvorak": "Set Dvorak; rm -rf /"})
