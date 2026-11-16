"""Select CI scopes from a complete git diff; uncertainty runs every scope."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ALL = {"core": True, "iic": True, "fisc": True}


def classify(paths: list[str]) -> dict[str, bool]:
    scopes = dict.fromkeys(ALL, False)
    for path in paths:
        # Both Docker targets read every workspace manifest, even with no source import.
        shared_manifest = path in {
            "packages/dfe/pyproject.toml",
            "packages/dfe/frontend/package.json",
            "frontend/package.json",
        }
        if shared_manifest:
            return ALL.copy()
        if path.startswith("packages/dfe/"):
            if not path.startswith("packages/dfe/docs/") and path != "packages/dfe/README.md":
                scopes["fisc"] = True
        elif (
            path.startswith(("iic_copilot/", "frontend/", "deploy/"))
            or path == "docker-compose.yml"
        ):
            scopes["iic"] = True
        elif path.startswith("docs/") or path.endswith(".md"):
            continue
        else:
            # Core, tests, locks, scripts, workflows, and unknown paths are shared.
            return ALL.copy()
    return scopes


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)


def select(event: dict, name: str, head: str) -> dict[str, bool]:
    if name not in {"push", "pull_request"}:
        return ALL.copy()
    if name == "pull_request":
        base = event["pull_request"]["base"]["sha"]
        head = event["pull_request"]["head"]["sha"]
    else:
        base = event.get("before", "")
    if not all(re.fullmatch(r"[0-9a-fA-F]{40}", sha) for sha in (base, head)):
        return ALL.copy()
    if base == "0" * 40:
        return ALL.copy()
    try:
        # PR uses the merge-base; push includes every commit since 'before'.
        revision = f"{base}...{head}" if name == "pull_request" else f"{base}..{head}"
        # No rename detection: both old and new paths must influence routing.
        changed = git("diff", "--no-renames", "--name-only", "-z", revision)
        return classify([p for p in changed.split("\0") if p])
    except subprocess.CalledProcessError:
        return ALL.copy()


def main() -> None:
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        scopes = select(event, os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_SHA"])
    except (KeyError, ValueError, OSError):
        scopes = ALL.copy()
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.writelines(f"{key}={str(value).lower()}\n" for key, value in scopes.items())
    print(json.dumps(scopes, sort_keys=True))


if __name__ == "__main__":
    main()
