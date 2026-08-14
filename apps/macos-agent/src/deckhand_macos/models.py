from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class LocalAction(StrEnum):
    APP_ENSURE_RUNNING = "mac.app.ensure_running"
    BROWSER_SESSION_OPEN = "browser.session.open"
    SHORTCUT_RUN = "mac.shortcut.run"


class LocalActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: LocalAction
    target: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")


class LocalActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: LocalAction
    target: str
    state: str
    verified: bool
    details: dict[str, str] = Field(default_factory=dict)
