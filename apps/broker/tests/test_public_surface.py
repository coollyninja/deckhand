"""The public-surface checker must actually detect the leak classes it claims to.

The review found several dead detections (a nonsense org phrase, invisible
Tailscale IPs and *.ts.net names). These tests seed each positive case so a
silently-dead pattern fails CI instead of passing quietly.
"""

import importlib.util
from pathlib import Path

import pytest

_CHECKER = Path(__file__).parents[3] / "scripts/check_public_surface.py"


def _load_findings():
    spec = importlib.util.spec_from_file_location("check_public_surface", _CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.findings


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("content", "needle"),
    [
        ("host = server.internal", "private DNS"),
        ("node = laptop.magic-fox.ts.net", "MagicDNS"),
        ("addr = 100.101.102.103", "CGNAT"),
        ("gw = 192.168.1.1", "private IPv4"),
        ("v6 = fd00:1234::1", "private IPv6"),
        ("org = tafylabs internal", "organization-specific"),
        ("org = batfang lab", "organization-specific"),
        (
            "-----BEGIN PRIVATE KEY-----\nMIIB...\n-----END PRIVATE KEY-----",
            "credential",
        ),
        ("token = tskey-auth-abcdefgh12345678", "credential"),
    ],
)
def test_checker_detects_leak(tmp_path: Path, content: str, needle: str) -> None:
    findings = _load_findings()
    problems = findings(_write(tmp_path, "leak.txt", content))
    assert any(needle in p for p in problems), f"expected to detect {needle!r} in {content!r}"


@pytest.mark.parametrize(
    "content",
    [
        "endpoint = api.example.invalid",  # .invalid placeholder is allowed
        "loopback = 127.0.0.1",  # loopback is fine
        "public = 8.8.8.8",  # public IP is fine
        "bind = 0.0.0.0",  # unspecified is fine
        'uuid = "com.coollyninja.deckhand.local"',  # reverse-DNS plugin UUID, not a host
        'bundle = "md.obsidian"',  # app bundle id, not a host
    ],
)
def test_checker_allows_clean_surface(tmp_path: Path, content: str) -> None:
    findings = _load_findings()
    assert findings(_write(tmp_path, "clean.txt", content)) == []


def test_checker_passes_on_the_real_repo() -> None:
    # Guards against a regression that would make the whole public surface fail.
    findings = _load_findings()
    root = Path(__file__).parents[3]
    problems: list[str] = []
    for path in root.glob("config/*.example.yaml"):
        problems.extend(findings(path))
    assert problems == []
