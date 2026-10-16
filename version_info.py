"""Marqueur de version déployée (anti-confusion Cloud).

Affiche le SHA git court du code réellement exécuté, pour vérifier
d'un coup d'œil que Streamlit Cloud a bien redéployé après un push.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
        sha = out.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parent,
        ).stdout.strip()
        return f"{sha}*" if sha and dirty else (sha or "n/a")
    except Exception:
        return "n/a"
