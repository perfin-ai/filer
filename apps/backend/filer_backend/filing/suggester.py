"""Destination-folder suggestion engine.

STUB: returns a fixed "records" folder first, then a couple of random real
folders. This is the seam for the real engine (docs/plan.md §7 — semantic
similarity + filename match + folder history); replace `suggest_folders`
without touching the runner/API.
"""

import random
from pathlib import Path

from filer_backend.filing.fs import LIBRARY_ROOT, list_subdirs
from filer_backend.storage.models import InboxFile

# Always-offered top suggestion (a real folder on this system).
_RECORDS_FOLDER = (
    "/Volumes/home/_Documents/_Records/_By Year/_2026/"
    "Banking/Credit/Fidelity/Visa 6665/Documents"
)


def _random_dirs(n: int) -> list[str]:
    """Pick `n` distinct real folders by random-walking down from the root."""
    out: list[str] = []
    seen: set[str] = set()
    for _ in range(n * 8):
        if len(out) >= n:
            break
        cur = Path(LIBRARY_ROOT)
        for _ in range(random.randint(1, 4)):
            subs = list_subdirs(cur)
            if not subs:
                break
            cur = random.choice(subs)
        s = str(cur)
        if s != LIBRARY_ROOT and s != _RECORDS_FOLDER and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def suggest_folders(inbox_file: InboxFile) -> list[tuple[str, float, str]]:
    """Return ranked (folder_path, confidence, rationale), highest first."""
    suggestions: list[tuple[str, float, str]] = [
        (
            _RECORDS_FOLDER,
            0.94,
            "Matches your Records filing for Fidelity Visa statements.",
        )
    ]
    for folder in _random_dirs(2):
        suggestions.append(
            (folder, round(random.uniform(0.45, 0.82), 2), "Another folder on this system.")
        )
    suggestions.sort(key=lambda s: s[1], reverse=True)
    return suggestions
