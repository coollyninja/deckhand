from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IPV4 = re.compile(r"(?<![A-Za-z0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![A-Za-z0-9.])")
PRIVATE_DNS = re.compile(
    r"(?i)\b[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]+)*\.(?:internal|lan|local)\b"
)
# Tailscale MagicDNS names (*.ts.net) are the most likely leak class for a
# Tailscale-primary deployment and are not caught by the .internal/.lan/.local
# rule above.
TAILNET_DNS = re.compile(r"(?i)\b[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]+)*\.ts\.net\b")
PRIVATE_IPV6 = re.compile(r"(?i)(?<![0-9a-f:])f[cd][0-9a-f]{2}:[0-9a-f:]+")
# Build the real organization needles from parts so the literals do not sit in
# this file verbatim, while still concatenating to strings that actually occur.
FORBIDDEN_PHRASES = ("tafy" + "labs", "tafy" + " labs", "batfang")
# Tailscale CGNAT range (100.64.0.0/10) is NOT flagged by ipaddress.is_private,
# so a Tailscale IP would otherwise pass clean. Check it explicitly.
TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")
# Obvious embedded-secret shapes: PEM private-key blocks and common API-token
# prefixes. Deliberately narrow to avoid false positives on public examples.
CREDENTIAL = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"
    r"|tskey-[a-z]+-[A-Za-z0-9]{8,}"
    r"|(?:xox[baprs]-[A-Za-z0-9-]{10,})"
    r"|(?:ghp_[A-Za-z0-9]{20,})"
)
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2"}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["/usr/bin/git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def findings(path: Path) -> list[str]:
    if not path.is_file():
        return []
    if path.suffix.lower() in SKIP_SUFFIXES:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    problems: list[str] = []
    lowered = text.lower()
    # The checker's own source and its test fixtures legitimately contain the
    # patterns it detects (seeded positive cases); skip them so it does not flag
    # itself or its own test corpus.
    if path.resolve() == Path(__file__).resolve():
        return []
    if path.name == "test_public_surface.py":
        return []
    for phrase in FORBIDDEN_PHRASES:
        if phrase in lowered:
            problems.append(f"organization-specific phrase {phrase!r}")
    for match in PRIVATE_DNS.finditer(text):
        # Reverse-DNS application identifiers (com.*/io.*/net.*/org.* … .local) are
        # Apple/Elgato bundle-UUID convention, not private hostnames.
        if re.match(r"^(?:com|io|net|org|md|app)\.", match.group(0), re.IGNORECASE):
            continue
        problems.append(f"private DNS name {match.group(0)!r}")
    for match in TAILNET_DNS.finditer(text):
        problems.append(f"tailnet MagicDNS name {match.group(0)!r}")
    for match in PRIVATE_IPV6.finditer(text):
        problems.append(f"private IPv6 address {match.group(0)!r}")
    for match in CREDENTIAL.finditer(text):
        problems.append(f"possible embedded credential {match.group(0)[:24]!r}...")
    for match in IPV4.finditer(text):
        try:
            address = ipaddress.ip_address(match.group(0))
        except ValueError:
            continue
        if address in TAILSCALE_CGNAT:
            problems.append(f"Tailscale CGNAT address {address}")
        elif (
            address.is_private
            and address.packed[0] != 0
            and not address.is_loopback
            and not address.is_unspecified
        ):
            problems.append(f"private IPv4 address {address}")
    return problems


def main() -> None:
    failures: list[str] = []
    for path in tracked_files():
        for problem in findings(path):
            failures.append(f"{path.relative_to(ROOT)}: {problem}")
    if failures:
        raise SystemExit("public-surface validation failed:\n" + "\n".join(failures))
    print("public-surface validation passed")


if __name__ == "__main__":
    main()
