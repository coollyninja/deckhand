from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LocalAction(StrEnum):
    # Apps / browser / shortcuts (original)
    APP_ENSURE_RUNNING = "mac.app.ensure_running"
    BROWSER_SESSION_OPEN = "browser.session.open"
    SHORTCUT_RUN = "mac.shortcut.run"
    # Audio / mic / meetings (§13.7)
    MIC_MUTE = "mac.mic.mute"
    MIC_UNMUTE = "mac.mic.unmute"
    MIC_TOGGLE = "mac.mic.toggle"
    MIC_STATE = "mac.mic.state"
    AUDIO_OUTPUT_SELECT = "mac.audio.output_select"
    AUDIO_INPUT_SELECT = "mac.audio.input_select"
    AUDIO_STATE = "mac.audio.state"
    # Focus / Pomodoro (§13.11)
    FOCUS_SET = "mac.focus.set"
    FOCUS_CLEAR = "mac.focus.clear"
    FOCUS_STATE = "mac.focus.state"
    POMODORO_START = "mac.pomodoro.start"
    POMODORO_STOP = "mac.pomodoro.stop"
    POMODORO_STATE = "mac.pomodoro.state"
    # Displays / desktop modes (§13.6)
    DISPLAY_MODE_APPLY = "mac.display.mode_apply"
    DISPLAY_STATE = "mac.display.state"
    SLEEP_INHIBIT_ON = "mac.sleep.inhibit_on"
    SLEEP_INHIBIT_OFF = "mac.sleep.inhibit_off"
    SLEEP_INHIBIT_STATE = "mac.sleep.inhibit_state"
    # Dev workspaces (§13.4)
    WORKSPACE_OPEN = "mac.workspace.open"
    # Recording / OBS (§13.7)
    OBS_SCENE_SET = "mac.obs.scene_set"
    OBS_RECORD_START = "mac.obs.record_start"
    OBS_RECORD_STOP = "mac.obs.record_stop"
    OBS_RECORD_STATE = "mac.obs.record_state"
    OBS_SOURCE_TOGGLE = "mac.obs.source_toggle"
    OBS_REPLAY_SAVE = "mac.obs.replay_save"


# Actions whose target is a free-form alias vs. actions that take no target.
_NO_TARGET_ACTIONS = frozenset(
    {
        LocalAction.MIC_MUTE,
        LocalAction.MIC_UNMUTE,
        LocalAction.MIC_TOGGLE,
        LocalAction.MIC_STATE,
        LocalAction.AUDIO_STATE,
        LocalAction.FOCUS_CLEAR,
        LocalAction.FOCUS_STATE,
        LocalAction.POMODORO_STOP,
        LocalAction.POMODORO_STATE,
        LocalAction.DISPLAY_STATE,
        LocalAction.SLEEP_INHIBIT_OFF,
        LocalAction.SLEEP_INHIBIT_STATE,
        LocalAction.OBS_RECORD_START,
        LocalAction.OBS_RECORD_STOP,
        LocalAction.OBS_RECORD_STATE,
        LocalAction.OBS_REPLAY_SAVE,
    }
)


def action_requires_target(action: LocalAction) -> bool:
    return action not in _NO_TARGET_ACTIONS


class LocalActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: LocalAction
    # A cataloged alias (e.g. an audio-device alias, a focus-mode alias). Actions
    # that operate on the whole machine (mute the mic, read state) use the reserved
    # alias "_". No arbitrary payload is ever accepted — the alias only indexes a
    # value in the operator inventory.
    target: str = Field(default="_", pattern=r"^[a-z_][a-z0-9_-]{0,63}$")


class LocalActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: LocalAction
    target: str
    state: str
    verified: bool
    details: dict[str, Any] = Field(default_factory=dict)
