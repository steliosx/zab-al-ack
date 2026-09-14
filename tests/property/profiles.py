"""Bounded Hypothesis profiles shared by pure and PostgreSQL properties."""
from __future__ import annotations

import os
from pathlib import Path

from hypothesis import Phase, seed, settings
from hypothesis.database import DirectoryBasedExampleDatabase

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED = 20260911


def profile_name() -> str:
    name = os.environ.get("ACK_HYPOTHESIS_PROFILE", "pr")
    if name not in {"pr", "nightly"}:
        raise ValueError("ACK_HYPOTHESIS_PROFILE must be pr or nightly")
    return name


def corpus_directory() -> Path:
    # Never put minimized examples beside source, where release archives can
    # accidentally turn diagnostic inputs into a public deliverable.
    path = ROOT / "build" / "private" / "hypothesis"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def chosen_seed() -> int | None:
    value = os.environ.get("ACK_HYPOTHESIS_SEED")
    return int(value) if value is not None else (DEFAULT_SEED if profile_name() == "pr" else None)


def property_seed():
    value = chosen_seed()
    return seed(value) if value is not None else lambda function: function


def property_settings(*, stateful: bool = False) -> settings:
    nightly = profile_name() == "nightly"
    return settings(
        max_examples=(75 if nightly else 8) if stateful else (200 if nightly else 40),
        stateful_step_count=60 if nightly else 20,
        deadline=None,  # SQL wall time is environmental, never a correctness oracle.
        database=DirectoryBasedExampleDatabase(corpus_directory() / "examples"),
        phases=(Phase.reuse, Phase.generate, Phase.target, Phase.shrink),
        print_blob=True,
        report_multiple_bugs=False,
    )
