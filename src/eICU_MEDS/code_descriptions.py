"""Packaged code descriptions for ``metadata/codes.parquet``.

eICU ships no code dictionary: its tables record names directly (``labName``, ``drugName``,
``treatmentString``), so earlier releases wrote no ``description`` at all (the code string was
the description). That is enough for a reader, but not for tooling that maps codes across
datasets or builds features from descriptions, so this module ships the documented meaning of
every code family, with the source of every row recorded in the tables themselves (see the
README section "Code metadata: descriptions").

Three routes, because MEDS-Extract attaches metadata differently depending on the code shape:

* **Lab codes** (``LAB//RESULT//{labname}//{unit}``): ``lab_descriptions.csv`` is keyed on the
  raw ``labname`` and joined by the ``_metadata`` blocks of the two ``lab`` events. Pre-MEDS
  writes it next to the raw tables as ``lab_descriptions.parquet``. The table lists only lab
  names that are public (the open eICU demo or MIT-LCP's ``eicu-code``), so it ships nothing
  from the credentialed release; a lab name outside it gets a generic description built from
  the code itself after the pipeline (:func:`apply_static_descriptions`).
* **Component-coded events** (patient events, diagnoses, medications, ...): ``_self``
  templates in ``configs/event_configs.yaml``; nothing to ship.
* **Literal codes** (vitals, weights, ``MEDS_BIRTH``, ...): ``static_code_descriptions.csv``.
  A literal code has no components for ``_metadata`` to join on, and the pre-existing
  ``codes.parquet`` route MEDS-Extract documents for this case is not reachable from the
  standard extraction pipeline (``extract_code_metadata`` reads its prior *metadata* stage's
  output, ``split_and_shard_subjects``, which writes no ``codes.parquet``). So the ETL fills
  these in after the pipeline, with :func:`apply_static_descriptions`.
"""

import logging
import os
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from meds import CodeMetadataSchema

logger = logging.getLogger(__name__)

METADATA_DIR = Path(__file__).parent / "metadata"
# Must match the `_metadata` prefix used by the `lab` events in `configs/event_configs.yaml`.
LAB_DESCRIPTIONS_PREFIX = "lab_descriptions"
LAB_DESCRIPTIONS_CSV = METADATA_DIR / "lab_descriptions.csv"
STATIC_DESCRIPTIONS_CSV = METADATA_DIR / "static_code_descriptions.csv"
# `LAB//{RESULT|SPECIMEN_DRAWN}//{labname}//{unit}`; units hold single slashes (`mg/dL`), never `//`.
LAB_CODE_PATTERN = r"^LAB//(RESULT|SPECIMEN_DRAWN)//(.+)//[^/]*(?:/[^/]+)*$"


def lab_fallback_description(code: pl.Expr) -> pl.Expr:
    """A generic description for lab codes whose name is not in the packaged lab table.

    It mirrors the ``_metadata`` templates of the two ``lab`` events, minus the table's
    description, and is built at run time from the code string alone. Non-lab codes give null.

    Examples:
        >>> codes = ["LAB//RESULT//Hgb//g/dL", "LAB//SPECIMEN_DRAWN//Hgb//UNK", "MEDS_BIRTH"]
        >>> pl.select(lab_fallback_description(pl.lit(pl.Series(codes)))).to_series().to_list()
        ["Lab result (eICU lab.labName 'Hgb')", "Lab specimen drawn (eICU lab.labName 'Hgb')", None]
    """
    kind = code.str.extract(LAB_CODE_PATTERN, 1)
    labname = code.str.extract(LAB_CODE_PATTERN, 2)
    label = pl.when(kind == "RESULT").then(pl.lit("Lab result")).otherwise(pl.lit("Lab specimen drawn"))
    return pl.when(labname.is_not_null()).then(pl.format("{} (eICU lab.labName '{}')", label, labname))


def read_lab_descriptions(path: Path = LAB_DESCRIPTIONS_CSV) -> pl.DataFrame:
    """Read the packaged lab-description table, as the ``_metadata`` join will see it.

    Every column stays a String (the join key is matched against the raw ``labname`` text), and
    an empty ``loinc_code`` cell is null, so the ``parent_codes`` expression in the config yields
    null rather than a dangling ``LOINC/`` for lab names without a bridged LOINC code.

    Examples:
        >>> df = read_lab_descriptions()
        >>> df.columns
        ['labname', 'description', 'loinc_code', 'source', 'source_detail']
        >>> df.filter(pl.col("labname") == "Hgb").select("description", "loinc_code").row(0)
        ('Hemoglobin', '718-7')
        >>> df.filter(pl.col("labname") == "MPV")["loinc_code"].item() is None
        True
        >>> df["labname"].is_duplicated().any()
        False
    """
    return pl.read_csv(path, infer_schema=False)


def read_static_code_descriptions(path: Path = STATIC_DESCRIPTIONS_CSV) -> pl.DataFrame:
    """Read the packaged descriptions of literal codes in the MEDS ``codes.parquet`` shape.

    Only ``code`` and ``description`` are emitted; the ``source`` column documents the table
    itself and is not written into the dataset.

    Examples:
        >>> df = read_static_code_descriptions()
        >>> df.columns
        ['code', 'description']
        >>> df.filter(pl.col("code") == "VITALS//PERIODIC//HEARTRATE")["description"].item()
        'Heart rate, beats per minute (vitalPeriodic.heartRate)'
        >>> df["code"].is_duplicated().any()
        False
    """
    return pl.read_csv(path, infer_schema=False).select("code", "description")


def write_lab_descriptions(output_dir: Path) -> Path:
    """Write the lab table where the extraction pipeline reads the ``lab_descriptions`` prefix.

    Examples:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as d:
        ...     fp = write_lab_descriptions(Path(d))
        ...     fp.name, pl.read_parquet(fp).schema["loinc_code"]
        ('lab_descriptions.parquet', String)
    """
    out_fp = Path(output_dir) / f"{LAB_DESCRIPTIONS_PREFIX}.parquet"
    read_lab_descriptions().write_parquet(out_fp)
    return out_fp


def apply_static_descriptions(codes_fp: Path, static: pl.DataFrame | None = None) -> int:
    """Fill the ``description`` of literal codes in a finished ``codes.parquet``, in place.

    Lab codes the packaged lab table does not describe get :func:`lab_fallback_description`.
    Only codes whose description is null are filled (an extracted description always wins,
    matching MEDS-Extract's own precedence), and only codes present in the file are touched:
    the vocabulary is never extended. The file is replaced atomically and is validated against
    the MEDS code-metadata schema before it is written: polars materializes strings as Arrow
    ``large_string``, which that schema rejects, so the MEDS columns are cast back to the types
    the pipeline itself writes (``string``, ``list<string>``).

    Args:
        codes_fp: The ``metadata/codes.parquet`` of a finished extraction.
        static: Override of the packaged table (``code``, ``description``), for tests.

    Returns:
        The number of codes whose description was filled.

    Examples:
        >>> import tempfile
        >>> static = pl.DataFrame({"code": ["MEDS_BIRTH", "NOT_EMITTED"], "description": ["Birth", "x"]})
        >>> codes = pl.DataFrame(
        ...     {"code": ["MEDS_BIRTH", "LAB//RESULT//Hgb//g/dL", "LAB//RESULT//newlab//%", "GENDER//Female"],
        ...      "description": [None, "Hemoglobin", None, None],
        ...      "parent_codes": [None, ["LOINC/718-7"], None, None]}
        ... )
        >>> with tempfile.TemporaryDirectory() as d:
        ...     fp = Path(d) / "codes.parquet"
        ...     codes.write_parquet(fp)
        ...     n = apply_static_descriptions(fp, static)
        ...     out = pl.read_parquet(fp)
        >>> n
        2
        >>> out.sort("code").select("code", "description").to_dicts()  # doctest: +NORMALIZE_WHITESPACE
        [{'code': 'GENDER//Female', 'description': None},
         {'code': 'LAB//RESULT//Hgb//g/dL', 'description': 'Hemoglobin'},
         {'code': 'LAB//RESULT//newlab//%', 'description': "Lab result (eICU lab.labName 'newlab')"},
         {'code': 'MEDS_BIRTH', 'description': 'Birth'}]
        >>> out.columns == codes.columns and out.height == codes.height
        True
    """
    codes_fp = Path(codes_fp)
    if static is None:
        static = read_static_code_descriptions()
    codes = pl.from_arrow(pq.read_table(codes_fp))
    if "description" not in codes.columns:
        codes = codes.with_columns(pl.lit(None, dtype=pl.String).alias("description"))
    filled = (
        codes.join(static.rename({"description": "_static"}), on="code", how="left")
        .with_columns(
            pl.coalesce("description", "_static", lab_fallback_description(pl.col("code"))).alias(
                "description"
            )
        )
        .drop("_static")
        .select(codes.columns)
    )
    n_filled = int(filled["description"].is_not_null().sum() - codes["description"].is_not_null().sum())

    out = filled.to_arrow()
    meds_types = {"code": pa.string(), "description": pa.string(), "parent_codes": pa.list_(pa.string())}
    out = out.cast(pa.schema([pa.field(f.name, meds_types.get(f.name, f.type)) for f in out.schema]))
    CodeMetadataSchema.validate(out)

    tmp_fp = codes_fp.with_suffix(".parquet.tmp")
    pq.write_table(out, tmp_fp)
    os.replace(tmp_fp, codes_fp)
    return n_filled
