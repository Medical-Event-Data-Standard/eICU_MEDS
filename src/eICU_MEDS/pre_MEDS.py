"""Pre-MEDS stage for eICU.

The 0.7 migration removed eICU's original pre-MEDS step entirely — the offset-to-pseudotime
derivation it existed for is now declared in ``configs/event_configs.yaml`` via
``_table.join`` + ``_table.cols``. This module does **not** bring that back. It performs no
data wrangling on any event table; it exists solely to assemble one small *metadata* side
table that the event config cannot build for itself.

Why it is needed
----------------
``diagnosis.icd9code`` maps one clinical problem (its ``diagnosisstring`` path) to the ICD
codes that encode it, comma-joined in a single cell::

    endocrine|glucose metabolism|diabetes mellitus|Type II|controlled  ->  250.00, E11.9

Those codes belong in ``parent_codes``, which MEDS-Extract reduces to a ``List(String)`` by
unioning across *metadata rows* sharing a join key. Producing those rows means turning one
comma-joined cell into one row per code — an explode. dftly has no list type and MESSY has
no row-multiplying construct, so this is the one thing that cannot be expressed in the
config (upstream: mmcdermott/dftly#87).

So the split of responsibilities is deliberately minimal:

* **here (Python)** — group to distinct ``(diagnosisstring, icd9code)`` pairs, explode the
  cell into one row per raw code, and deduplicate.
* **the config (dftly)** — classify each raw code into ``ICD9CM/...`` / ``ICD10CM/...``.

No regex, no vocabulary knowledge, and no formatting decisions live in this file.

Everything else is passed through untouched: every raw table is symlinked (or copied, with
``do_copy``) into the output directory, because MEDS-Extract reads its input from a single
directory and this stage adds one file to it.
"""

import logging
import os
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

# The table the mapping is derived from, and the name the derived table is written under.
# `DX_MAP_PREFIX` must match the `_metadata` source prefix in `configs/event_configs.yaml`.
DIAGNOSIS_PREFIX = "diagnosis"
DX_MAP_PREFIX = "dx_icd_map"

# eICU joins multiple codes with ", ". Splitting on the bare comma would also split inside
# a code, and splitting on a regex would be needless here — the separator is literal.
CODE_SEPARATOR = ", "


def build_dx_icd_map(diagnosis: pl.LazyFrame) -> pl.LazyFrame:
    """Explode ``icd9code`` into one row per ``(diagnosisstring, icd_code)`` pair.

    ``icd9code`` is a vendor lookup keyed on ``diagnosisstring``, not a per-row coding
    decision, so the distinct pairs are taken first — that reduces ~2.7M raw rows to a few
    thousand before any string work.

    Deduplication is load-bearing rather than cosmetic: some cells carry literal repeats
    (``486, 486, 486, 486, 486, J18.9`` is real data, an artifact of the source lookup
    table), and those must not become repeated parent codes.

    Args:
        diagnosis: The raw ``diagnosis`` table, with at least ``diagnosisstring`` and
            ``icd9code``.

    Returns:
        A frame of unique ``(diagnosisstring, icd_code)`` pairs. Rows with no code are
        dropped — an uncoded problem simply has no parents.

    Examples:
        >>> raw = pl.LazyFrame(
        ...     {
        ...         "diagnosisstring": ["a|b", "a|b", "c|d", "e|f", "g|h"],
        ...         "icd9code": ["401.9, I10", "401.9, I10", "038.9, 518.81", "", None],
        ...     }
        ... )
        >>> build_dx_icd_map(raw).collect().sort("diagnosisstring", "icd_code")
        shape: (4, 2)
        ┌─────────────────┬──────────┐
        │ diagnosisstring ┆ icd_code │
        │ ---             ┆ ---      │
        │ str             ┆ str      │
        ╞═════════════════╪══════════╡
        │ a|b             ┆ 401.9    │
        │ a|b             ┆ I10      │
        │ c|d             ┆ 038.9    │
        │ c|d             ┆ 518.81   │
        └─────────────────┴──────────┘

        Repeated codes within a cell collapse to one row:

        >>> raw = pl.LazyFrame(
        ...     {"diagnosisstring": ["p|q"], "icd9code": ["486, 486, 486, J18.9"]}
        ... )
        >>> build_dx_icd_map(raw).collect().sort("icd_code")
        shape: (2, 2)
        ┌─────────────────┬──────────┐
        │ diagnosisstring ┆ icd_code │
        │ ---             ┆ ---      │
        │ str             ┆ str      │
        ╞═════════════════╪══════════╡
        │ p|q             ┆ 486      │
        │ p|q             ┆ J18.9    │
        └─────────────────┴──────────┘
    """
    return (
        diagnosis.select("diagnosisstring", "icd9code")
        .drop_nulls("diagnosisstring")
        .unique()
        .with_columns(pl.col("icd9code").fill_null("").str.split(CODE_SEPARATOR).alias("icd_code"))
        .explode("icd_code")
        .with_columns(pl.col("icd_code").str.strip_chars())
        .filter(pl.col("icd_code") != "")
        .select("diagnosisstring", "icd_code")
        .unique()
    )


def _link_or_copy(src: Path, dst: Path, do_copy: bool) -> None:
    """Place ``src`` at ``dst``, by copy or symlink, replacing anything already there."""
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    if do_copy:
        import shutil

        shutil.copy2(src, dst)
    else:
        dst.symlink_to(os.path.relpath(src, dst.parent))


def main(
    input_dir: Path,
    output_dir: Path,
    do_overwrite: bool = False,
    do_copy: bool = False,
) -> None:
    """Assemble the pre-MEDS input directory.

    Passes every raw table through untouched and adds the derived
    ``dx_icd_map.parquet``. Nothing here modifies event data.

    Args:
        input_dir: Directory holding the raw eICU download.
        output_dir: Directory the extraction pipeline will read from.
        do_overwrite: Rebuild the derived table even if it already exists.
        do_copy: Copy raw tables instead of symlinking them.

    Raises:
        FileNotFoundError: If the raw ``diagnosis`` table is missing.
    """
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_files = sorted(input_dir.glob("*.csv.gz"))
    if not raw_files:
        raise FileNotFoundError(f"No *.csv.gz files found under {input_dir.resolve()!s}.")

    verb = "Copying" if do_copy else "Symlinking"
    logger.info(f"{verb} {len(raw_files)} raw tables into {output_dir.resolve()!s}.")
    for fp in raw_files:
        _link_or_copy(fp, output_dir / fp.name, do_copy)

    out_fp = output_dir / f"{DX_MAP_PREFIX}.parquet"
    if out_fp.is_file() and not do_overwrite:
        logger.info(f"{out_fp.resolve()!s} exists; skipping (pass do_overwrite=True to rebuild).")
        return

    dx_fp = input_dir / f"{DIAGNOSIS_PREFIX}.csv.gz"
    if not dx_fp.is_file():
        raise FileNotFoundError(
            f"{dx_fp.resolve()!s} not found; cannot build the {DX_MAP_PREFIX} metadata table."
        )

    logger.info(f"Building {DX_MAP_PREFIX} from {dx_fp.resolve()!s}.")
    dx_map = build_dx_icd_map(pl.scan_csv(dx_fp, infer_schema_length=None)).collect()
    dx_map.write_parquet(out_fp)
    logger.info(
        f"Wrote {len(dx_map):,} (diagnosisstring, icd_code) pairs covering "
        f"{dx_map['diagnosisstring'].n_unique():,} problems to {out_fp.resolve()!s}."
    )
