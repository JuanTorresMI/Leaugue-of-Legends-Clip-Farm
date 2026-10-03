"""Guard rail for a cloud-hosted repo: fail the suite if a secret or local-state file is tracked.

Real credentials live in .env and data/ (both gitignored). This catches the ways they leak
anyway: a `git add -f`, a token pasted into code or docs, or a renamed secrets file.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Shapes of real credentials for the services this project talks to.
_SECRET_PATTERNS = {
    "Riot API key": re.compile(r"RGAPI-[0-9a-f]{8}-[0-9a-f]{4}-"),
    "Facebook access token": re.compile(r"\bEAA[A-Za-z0-9]{40,}"),
    "Google OAuth client secret": re.compile(r"GOCSPX-[A-Za-z0-9_-]{10,}"),
    "Google access token": re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}"),
    "Google refresh token": re.compile(r"\b1//0[A-Za-z0-9_-]{30,}"),
}

_FORBIDDEN_PATHS = re.compile(
    r"(^|/)(\.env|riot_key\.txt|[^/]*token[^/]*\.json|client_secret[^/]*\.json)$"
    r"|^data/(?!\.gitkeep$)|\.(sqlite3|db|pem|key|mp4|mov|mkv)$"
)


def _tracked_files() -> list[str]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def test_no_secret_or_local_state_files_are_tracked():
    bad = [p for p in _tracked_files() if _FORBIDDEN_PATHS.search(p)]
    assert not bad, f"Secret/local-state files are tracked -- `git rm --cached` them: {bad}"


def test_no_credentials_in_tracked_text():
    hits = []
    for rel in _tracked_files():
        path = ROOT / rel
        if not path.is_file() or path.suffix in {".ttf", ".jpg", ".png"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{rel}: {name}" for name, rx in _SECRET_PATTERNS.items() if rx.search(text)]
    assert not hits, f"Possible credentials committed: {hits}"
