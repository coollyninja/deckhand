"""macOS executor: alias-only safety + correct dispatch, with subprocess faked."""

from typing import Any

import pytest
from deckhand_macos.config import MacInventory, ObsSettings
from deckhand_macos.executor import LocalActionError, MacExecutor
from deckhand_macos.models import LocalAction, LocalActionRequest


def _inventory() -> MacInventory:
    return MacInventory(
        apps={"terminal": "com.apple.Terminal"},
        audio_outputs={"speakers": "MacBook Speakers"},
        audio_inputs={"builtin": "MacBook Microphone"},
        focus_modes={"work": "Set Work Focus", "_clear": "Clear Focus"},
        pomodoro_routines={"deep": "Start Deep Focus", "_stop": "Stop Focus"},
        display_modes={"office": "Office Layout"},
        workspaces={"vault": "~/Documents/Vaults/example"},
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
