"""Pins the NUMERIC-GUARD regex in ``configs/event_configs.yaml`` to polars' semantics.

Several eICU columns are ``VARCHAR`` in the upstream DDL and carry non-numeric junk, so
the config routes them through ``regex_extract(<guard>, $col)::float64`` before they
become ``numeric_value``. That guard must accept **exactly** what polars'
``cast(Float64, strict=False)`` accepts — the coercion the pre-0.7 pipeline applied.

A too-narrow guard is the dangerous failure mode: it does not error, it silently nulls
real measurements (an earlier revision dropped ``1e5``, ``25.``, ``+5``, and inf/NaN).
The demo data contains none of those, so demo-based validation cannot catch it. This
test instead fuzzes the two implementations against each other over an adversarial
corpus, and reads the pattern out of the shipped config so the two cannot drift apart.
"""

import itertools
import random
import re

import polars as pl
import pytest

from eICU_MEDS import EVENT_CFG

# Matches the argument of any `regex_extract("<pattern>", $col)` guard in the config.
_GUARD_RE = re.compile(r'regex_extract\("(?P<pattern>\^\[\+-\].*?)",\s*\$')


def _guard_patterns() -> set[str]:
    """Every distinct NUMERIC-GUARD pattern shipped in the event config."""
    raw = EVENT_CFG.read_text()
    return {m.group("pattern") for m in _GUARD_RE.finditer(raw)}


def _corpus() -> list[str]:
    """Adversarial strings: structured combinatorics, clinical junk, and random noise."""
    signs = ["", "-", "+"]
    ints = ["", "0", "7", "25", "1000", "0007"]
    dots = ["", "."]
    fracs = ["", "0", "5", "25"]
    exps = ["", "e5", "E5", "e+5", "e-3", "E-03", "e", "e+", "e5.5"]

    out = {f"{s}{i}{d}{f}{e}" for s, i, d, f, e in itertools.product(signs, ints, dots, fracs, exps)}
    out |= {
        # inf/nan spellings polars accepts
        "inf", "INF", "-inf", "Infinity", "infinity", "nan", "NaN", "NAN", "-NaN",
        # real eICU-shaped junk
        "1,234", "5,000 UNITS", "1000 MG", "1 SPRAY", "> 89", "< 5", "ERROR", "", " ",
        "  25", "25  ", "1_000", "0x1A", "1.2.3", "--5", "+-5", ".", "-.", "e5", "1e",
        "N/A", "null", "NULL", "None", "--", "1e400", "-1e400", "0.0000000001",
        "99999999999999999999", "1.7976931348623157e308",
    }  # fmt: skip

    rng = random.Random(0)
    alphabet = "0123456789+-.eE, <>/nafiNAFI"
    out |= {"".join(rng.choice(alphabet) for _ in range(rng.randint(1, 7))) for _ in range(4000)}
    return sorted(out)


def _classify(x: float | None) -> str:
    """NaN != NaN, so compare by equivalence class rather than by value."""
    if x is None:
        return "null"
    return "nan" if x != x else repr(x)


def test_config_ships_exactly_one_numeric_guard():
    """All guards must be the same pattern — divergent copies are how drift starts."""
    patterns = _guard_patterns()
    assert len(patterns) == 1, f"expected one shared guard pattern, found {len(patterns)}: {patterns}"


@pytest.mark.parametrize("pattern", sorted(_guard_patterns()))
def test_guard_matches_polars_lenient_cast(pattern):
    """``regex_extract(guard, x)::float64`` must equal ``cast(Float64, strict=False)``."""
    corpus = _corpus()
    s = pl.Series("v", corpus, dtype=pl.String)

    expected = s.cast(pl.Float64, strict=False).to_list()
    # `.str.extract(pattern, 0)` is what dftly's `regex_extract` lowers to with no
    # group_index: the whole match, or null when the pattern does not match.
    actual = s.str.extract(pattern, 0).cast(pl.Float64, strict=False).to_list()

    mismatches = [
        (v, e, a) for v, e, a in zip(corpus, expected, actual, strict=True) if _classify(e) != _classify(a)
    ]
    assert not mismatches, f"{len(mismatches)} divergence(s) from polars, e.g. {mismatches[:5]}"


@pytest.mark.parametrize("pattern", sorted(_guard_patterns()))
def test_guard_keeps_values_a_narrow_pattern_would_drop(pattern):
    """Regression pins for the specific values an earlier, too-narrow guard nulled."""
    kept = {"1e5": 1e5, "1.5E-3": 1.5e-3, "25.": 25.0, "+5": 5.0, ".5": 0.5}
    s = pl.Series("v", list(kept), dtype=pl.String)
    got = s.str.extract(pattern, 0).cast(pl.Float64, strict=False).to_list()
    assert got == list(kept.values())


@pytest.mark.parametrize("pattern", sorted(_guard_patterns()))
def test_guard_rejects_clinical_free_text(pattern):
    """The junk the guard exists for must still become null rather than erroring."""
    junk = ["1000 MG", "5,000 UNITS", "1 SPRAY", "> 89", "ERROR", "", "N/A"]
    s = pl.Series("v", junk, dtype=pl.String)
    assert s.str.extract(pattern, 0).cast(pl.Float64, strict=False).to_list() == [None] * len(junk)
