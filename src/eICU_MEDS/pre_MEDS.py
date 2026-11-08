"""Pre-MEDS for eICU: the ``eicu_pre_meds`` pre-stage of the ``eICU`` spec.

The 0.7 migration removed eICU's original pre-MEDS step entirely — the offset-to-pseudotime
derivation it existed for is now declared in ``configs/event_configs.yaml`` via
``_table.joins`` + ``_table.cols``. This module does **not** bring that back. It performs no
data wrangling on any event table; it writes the few tables the event config reads that the raw
release does not have (:func:`stage_tables`), as a MEDS-Extract pre-stage: ``meds-extract-run``
runs it before ``convert_to_parquet`` and reads those tables from its directory instead of the raw
one, which it never modifies.

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

The stage also places the packaged lab-description table, and the demo's ``infusiondrug`` table
under the release spelling when the raw directory has only the demo's.
"""

import logging
import os
from pathlib import Path

import polars as pl
from MEDS_transforms.stages import Stage
from omegaconf import DictConfig

logger = logging.getLogger(__name__)

# The name `pyproject.toml` registers this module's pre-stage under (`MEDS_transforms.stages`), and
# the spec lists in `etl.pre_stages`.
STAGE_NAME = "eicu_pre_meds"

# The packaged lab-description table, and the name it is staged under. `LAB_DESCRIPTIONS_PREFIX`
# must match the `lab` table's join in `configs/event_configs.yaml`.
LAB_DESCRIPTIONS_CSV = Path(__file__).parent / "metadata" / "lab_descriptions.csv"
LAB_DESCRIPTIONS_PREFIX = "lab_descriptions"

# The table the mapping is derived from, and the name the derived table is written under.
# `DX_MAP_PREFIX` must match the `_metadata` source prefix in `configs/event_configs.yaml`.
DIAGNOSIS_PREFIX = "diagnosis"
DX_MAP_PREFIX = "dx_icd_map"


# eICU joins multiple codes with ", ". Splitting on the bare comma would also split inside
# a code, and splitting on a regex would be needless here — the separator is literal.
CODE_SEPARATOR = ", "

# The demo ships `infusiondrug.csv.gz`; the event config reads the release spelling. The
# download layer renames it, but a demo fetched any other way (`do_download=False`) still
# carries the lowercase name, so normalize it here too.
RAW_RENAMES = {"infusiondrug.csv.gz": "infusionDrug.csv.gz"}


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
        A frame of unique ``(diagnosisstring, icd_code)`` pairs, sorted. Rows with no code are
        dropped — an uncoded problem simply has no parents. MEDS-Extract keeps a metadata
        table's row order in ``parent_codes``, and ``unique`` alone leaves that order to chance.

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
        .explode("icd_code", empty_as_null=True)
        .with_columns(pl.col("icd_code").str.strip_chars())
        .filter(pl.col("icd_code") != "")
        .select("diagnosisstring", "icd_code")
        .unique()
        .sort("diagnosisstring", "icd_code")
    )


def read_lab_descriptions(path: Path = LAB_DESCRIPTIONS_CSV) -> pl.DataFrame:
    """Read the packaged lab-description table, as the ``lab`` table's join will see it.

    eICU ships no code dictionary, so the table gives the documented meaning of each lab name. It
    lists only lab names that are public (the open eICU demo, or MIT-LCP's ``eicu-code``), so it
    ships nothing from the credentialed release; the config describes a lab name outside it with a
    generic description. Every column stays a String (the join key is matched against the raw
    ``labname`` text). The ``source`` and ``source_detail`` columns document each row and are not
    joined. The table carries descriptions only: LOINC links for lab names are a suMEDS crosswalk
    (``eicu/lab_loinc``, applied with ``sumeds_rules.yaml``), not part of the ETL output.

    Examples:
        >>> df = read_lab_descriptions()
        >>> df.columns
        ['labname', 'description', 'source', 'source_detail']
        >>> df.filter(pl.col("labname") == "Hgb")["description"].item()
        'Hemoglobin'
        >>> df["labname"].is_duplicated().any()
        False
    """
    return pl.read_csv(path, infer_schema=False)


def write_lab_descriptions(output_dir: Path) -> Path:
    """Write the lab table where the extraction reads the ``lab_descriptions`` prefix.

    Examples:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as d:
        ...     fp = write_lab_descriptions(Path(d))
        ...     fp.name, pl.read_parquet(fp).schema["labname"]
        ('lab_descriptions.parquet', String)
    """
    out_fp = Path(output_dir) / f"{LAB_DESCRIPTIONS_PREFIX}.parquet"
    read_lab_descriptions().write_parquet(out_fp)
    return out_fp


def _replace_with_symlink(target: Path, link: Path) -> None:
    """Point ``link`` at ``target`` (absolute), replacing anything already at ``link``."""
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target.resolve())


def stage_tables(input_dir: Path, output_dir: Path) -> list[Path]:
    """Write the tables the event config reads that the raw release does not have.

    These are the ``eicu_pre_meds`` pre-stage's overlay tables: MEDS-Extract looks a source prefix
    up in this directory before the raw one, and the raw directory is never modified.

    * ``dx_icd_map.parquet``, built from the raw ``diagnosis`` table (:func:`build_dx_icd_map`);
    * ``lab_descriptions.parquet``, the packaged lab-description table;
    * ``infusionDrug.csv.gz``, a link to the demo's ``infusiondrug.csv.gz``, only when the raw
      directory has the demo spelling and not the release one (a demo fetched other than with
      ``meds-extract-download``, which renames it).

    Every table is rewritten on each run, so a re-run gives the same output.

    Args:
        input_dir: Directory holding the raw eICU-CRD tables.
        output_dir: The directory to write the tables into.

    Returns:
        The paths written.

    Raises:
        FileNotFoundError: If the raw ``diagnosis`` table is missing.

    Examples:
        >>> import gzip, tempfile
        >>> with tempfile.TemporaryDirectory() as d:
        ...     raw, out = Path(d) / "raw", Path(d) / "out"
        ...     raw.mkdir()
        ...     csv = 'diagnosisstring,icd9code\\np|q,"486, J18.9"\\n'.encode()
        ...     _ = (raw / "diagnosis.csv.gz").write_bytes(gzip.compress(csv))
        ...     _ = (raw / "infusiondrug.csv.gz").write_bytes(gzip.compress(b"a\\n1\\n"))
        ...     sorted(fp.name for fp in stage_tables(raw, out))
        ...     pl.read_parquet(out / "dx_icd_map.parquet").sort("icd_code")["icd_code"].to_list()
        ['dx_icd_map.parquet', 'infusionDrug.csv.gz', 'lab_descriptions.parquet']
        ['486', 'J18.9']
    """
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []

    # Exact names: on a case-insensitive filesystem `Path.exists()` would report the release
    # spelling as present when only the demo's lowercase file is.
    raw_names = {fp.name for fp in input_dir.iterdir()}
    for raw_name, name in RAW_RENAMES.items():
        if raw_name in raw_names and name not in raw_names:
            _replace_with_symlink(input_dir / raw_name, output_dir / name)
            written.append(output_dir / name)

    # Always rewritten: the table ships with the package, so it must track the installed version.
    lab_fp = write_lab_descriptions(output_dir)
    logger.info(f"Wrote the packaged lab-description table to {lab_fp.resolve()!s}.")
    written.append(lab_fp)

    dx_fp = input_dir / f"{DIAGNOSIS_PREFIX}.csv.gz"
    if not dx_fp.is_file():
        raise FileNotFoundError(
            f"{dx_fp.resolve()!s} not found; cannot build the {DX_MAP_PREFIX} metadata table."
        )
    logger.info(f"Building {DX_MAP_PREFIX} from {dx_fp.resolve()!s}.")
    dx_map = build_dx_icd_map(pl.scan_csv(dx_fp, infer_schema_length=None)).collect()
    out_fp = output_dir / f"{DX_MAP_PREFIX}.parquet"
    tmp_fp = output_dir / f".{DX_MAP_PREFIX}.parquet.tmp"
    dx_map.write_parquet(tmp_fp)
    os.replace(tmp_fp, out_fp)
    logger.info(
        f"Wrote {len(dx_map):,} (diagnosisstring, icd_code) pairs covering "
        f"{dx_map['diagnosisstring'].n_unique():,} problems to {out_fp.resolve()!s}."
    )
    written.append(out_fp)
    return written


@Stage.register(is_metadata=False, stage_name=STAGE_NAME, default_config={})
def main(cfg: DictConfig) -> None:
    """Stage the tables the eICU event config reads beyond the raw release.

    The ``eicu_pre_meds`` pre-stage of the registered ``eICU`` spec (``etl.pre_stages``). It reads
    the raw tables from ``cfg.input_dir`` and writes :func:`stage_tables` into its own directory,
    ``cfg.stage_cfg.output_dir``, which ``convert_to_parquet`` and ``extract_code_metadata`` search
    before ``input_dir``. It takes no options.
    """
    if cfg.worker != 0:
        return
    stage_tables(Path(cfg.input_dir), Path(cfg.stage_cfg.output_dir))
