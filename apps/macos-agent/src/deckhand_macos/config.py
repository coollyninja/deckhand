import re
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


_ALIAS_VALUE = re.compile(r"^[A-Za-z0-9 ._-]{1,128}$")
# App-command bundle-id shape: letters, digits, dots, dashes only. Conservative
# on purpose — nothing here reaches a shell, but defense-in-depth keeps a
# mistyped config from smuggling anything odd into the AppleScript frame.
_BUNDLE_ID = re.compile(r"^[A-Za-z0-9.-]+$")
# The AppleScript keystroke clause is operator-authored, so we allow ONLY the
# characters a `keystroke "..."` / `key code N using {...}` clause needs. This is
# defense-in-depth against a config that tries to smuggle arbitrary AppleScript:
# it rejects `;`, backticks, newlines, `&`, and anything outside this charset,
# and a word allowlist below rejects `do shell script` / a second `tell`.
_KEYSTROKE_VALUE = re.compile(r'^[A-Za-z0-9 "{},]+$')
# The only bare words a legitimate keystroke clause needs. Any other alphabetic
# run (e.g. "shell", "script", "tell", "application") means the value is trying
# to be more than a keystroke, so we reject it.
_KEYSTROKE_WORDS = frozenset(
    {
        "keystroke",
        "key",
        "code",
        "using",
        "command",
        "option",
        "control",
        "shift",
        "down",
    }
)


class ObsSettings(StrictModel):
    """Connection to a local OBS instance via obs-websocket (v5)."""

    host: str = "127.0.0.1"
    port: int = Field(default=4455, ge=1, le=65535)
    password_file: Path | None = None
    scenes: dict[str, str] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)

    @field_validator("scenes", "sources")
    @classmethod
    def validate_names(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not _ALIAS_VALUE.fullmatch(name) for name in value.values()):
            raise ValueError("OBS scene/source names contain unsupported characters")
        return value


class AppCommand(StrictModel):
    """An operator-declared 'activate app + send this keystroke' command.

    ``bundle_id`` is the target app's bundle identifier (e.g. "org.gimp.gimp").
    ``keystroke`` is an AppleScript keystroke clause the operator authored (e.g.
    ``keystroke "e" using {command down, shift down}``) — NOT a raw key combo from
    the request. Both are charset-validated here so a config cannot smuggle
    arbitrary AppleScript, and the executor wraps ``keystroke`` in a fixed
    System-Events frame so nothing else can execute.
    """

    bundle_id: str
    keystroke: str

    @field_validator("bundle_id")
    @classmethod
    def validate_bundle_id(cls, value: str) -> str:
        if not _BUNDLE_ID.fullmatch(value):
            raise ValueError("app command bundle_id contains unsupported characters")
        return value

    @field_validator("keystroke")
    @classmethod
    def validate_keystroke(cls, value: str) -> str:
        if not _KEYSTROKE_VALUE.fullmatch(value):
            raise ValueError("app command keystroke contains unsupported characters")
        # The quoted literal is the key character(s) being typed — arbitrary by
        # design (e.g. keystroke "e"). Strip quoted spans, then require every
        # remaining bare word to be part of a legitimate keystroke clause. This
        # catches `do shell script`, a second `tell`, `application`, etc. without
        # rejecting the typed character.
        outside_quotes = re.sub(r'"[^"]*"', " ", value)
        for word in re.findall(r"[A-Za-z]+", outside_quotes):
            if word.lower() not in _KEYSTROKE_WORDS:
                raise ValueError(f"app command keystroke has an unexpected word {word!r}")
        return value


class MacInventory(StrictModel):
    apps: dict[str, str] = Field(default_factory=dict)
    browser_sessions: dict[str, str] = Field(default_factory=dict)
    shortcuts: dict[str, str] = Field(default_factory=dict)
    # Audio devices, keyed by logical alias → the device name macOS reports.
    audio_inputs: dict[str, str] = Field(default_factory=dict)
    audio_outputs: dict[str, str] = Field(default_factory=dict)
    # Focus modes, keyed by alias → the exact Focus name (or a Shortcut name that
    # sets it, since macOS has no first-party Focus CLI).
    focus_modes: dict[str, str] = Field(default_factory=dict)
    # Pomodoro routines, keyed by alias → a Shortcut name that starts the routine.
    pomodoro_routines: dict[str, str] = Field(default_factory=dict)
    # Display-mode layouts, keyed by alias → a Shortcut name (or displayplacer
    # profile) that applies the layout.
    display_modes: dict[str, str] = Field(default_factory=dict)
    # Dev workspaces, keyed by alias → an absolute path to open (vault/project).
    workspaces: dict[str, str] = Field(default_factory=dict)
    # Keyboard input sources, keyed by alias → the Shortcut NAME the operator
    # created to select that input source (e.g. "Set Dvorak"). macOS has no safe
    # CLI to switch the active input source, so — exactly like focus/display
    # modes — the value is a Shortcut name run via `shortcuts run`, NOT a raw
    # input-source id.
    input_sources: dict[str, str] = Field(default_factory=dict)
    # App commands, keyed by alias → an AppCommand (bundle id + declared
    # AppleScript keystroke). One generic action covers GIMP/Blender/DaVinci
    # menu commands without per-app code.
    app_commands: dict[str, AppCommand] = Field(default_factory=dict)
    obs: ObsSettings = Field(default_factory=ObsSettings)

    @field_validator("apps")
    @classmethod
    def validate_apps(cls, value: dict[str, str]) -> dict[str, str]:
        pattern = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
        if any(not pattern.fullmatch(bundle_id) for bundle_id in value.values()):
            raise ValueError("app values must be bundle identifiers")
        return value

    @field_validator("browser_sessions")
    @classmethod
    def validate_urls(cls, value: dict[str, str]) -> dict[str, str]:
        for url in value.values():
            parsed = urlparse(url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                raise ValueError("browser session values must be HTTP(S) URLs")
        return value

    @field_validator("audio_inputs", "audio_outputs", "focus_modes")
    @classmethod
    def validate_device_names(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not _ALIAS_VALUE.fullmatch(name) for name in value.values()):
            raise ValueError("device/mode names contain unsupported characters")
        return value

    @field_validator("shortcuts", "pomodoro_routines", "display_modes", "input_sources")
    @classmethod
    def validate_shortcut_names(cls, value: dict[str, str]) -> dict[str, str]:
        # input_sources values are Shortcut names (see field comment), so they
        # share the shortcut-name charset — not raw input-source ids.
        if any(not _ALIAS_VALUE.fullmatch(name) for name in value.values()):
            raise ValueError("shortcut names contain unsupported characters")
        return value

    @field_validator("workspaces")
    @classmethod
    def validate_workspace_paths(cls, value: dict[str, str]) -> dict[str, str]:
        for path in value.values():
            if not path.startswith("/") and not path.startswith("~"):
                raise ValueError("workspace values must be absolute paths")
        return value


class MacSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DECKHAND_MACOS_", extra="forbid")

    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=19471, ge=1024, le=65535)
    inventory_path: Path = Path("config/macos.yaml")
    token_file: Path

    @field_validator("inventory_path", "token_file")
    @classmethod
    def expand_paths(cls, value: Path) -> Path:
        # Operators naturally write ~/.deckhand/... in env vars and config; Path
        # does not expand that, and every read site would fail with ENOENT.
        return value.expanduser()


def load_inventory(path: Path) -> MacInventory:
    raw = yaml.safe_load(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("macOS inventory must be a mapping")
    return MacInventory.model_validate(raw)
