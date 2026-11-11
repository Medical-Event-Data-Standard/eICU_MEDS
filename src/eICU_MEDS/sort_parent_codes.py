"""The ``eicu_sort_parent_codes`` post-stage: sort the ICD parents of each code.

MEDS-Extract lists a code's ``parent_codes`` in the row order of the metadata table they come
from. For eICU that is ``dx_icd_map``, sorted by the raw ICD code (``pre_MEDS.build_dx_icd_map``),
while the parents themselves are the prefixed codes the event config makes of them
(``ICD9CM/…``, ``ICD10CM/…``). This stage sorts each list by the parent code itself, as releases up
to 0.2.0 did, so the lists read the same way across codes and releases. It runs after the canonical
pipeline and before ``sumeds_enrich``, which keeps the ETL's parents first and appends its own.
"""

import logging
import os
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
from MEDS_transforms.stages import Stage
from omegaconf import DictConfig

logger = logging.getLogger(__name__)

# The name `pyproject.toml` registers this module's post-stage under (`MEDS_transforms.stages`),
# and the spec lists in `etl.post_stages`.
STAGE_NAME = "eicu_sort_parent_codes"


def sort_parent_codes(codes_fp: Path, out_fp: Path | None = None) -> bool:
    """Sort the ``parent_codes`` of every code in ``codes.parquet``, keeping the file's schema.

    A file suMEDS already enriched (it has ``parent_names``) is left alone: suMEDS appends its
    parents after the ETL's, and sorting again would mix the two.

    Args:
        codes_fp: The ``metadata/codes.parquet`` to read.
        out_fp: Where to write the result, atomically. Defaults to ``codes_fp``.

    Returns:
        Whether the file was written.

    Examples:
        >>> import tempfile
        >>> import pyarrow as pa
        >>> codes = pa.table(
        ...     {
        ...         "code": ["DIAGNOSIS//x", "MEDS_BIRTH"],
        ...         "description": ["x", "Birth"],
        ...         "parent_codes": [["ICD9CM/486", "ICD10CM/J18.9"], None],
        ...     }
        ... )
        >>> with tempfile.TemporaryDirectory() as d:
        ...     fp = Path(d) / "codes.parquet"
        ...     pq.write_table(codes, fp)
        ...     sort_parent_codes(fp)
        ...     out = pq.read_table(fp)
        True
        >>> out["parent_codes"].to_pylist()
        [['ICD10CM/J18.9', 'ICD9CM/486'], None]
        >>> out.schema == codes.schema
        True
    """
    codes_fp = Path(codes_fp)
    out_fp = codes_fp if out_fp is None else Path(out_fp)
    table = pq.read_table(codes_fp)
    if "parent_codes" not in table.column_names or "parent_names" in table.column_names:
        return False
    codes = pl.from_arrow(table).with_columns(pl.col("parent_codes").list.sort())
    tmp_fp = out_fp.with_name(f".{out_fp.name}.tmp")
    pq.write_table(codes.to_arrow().cast(table.schema), tmp_fp)
    os.replace(tmp_fp, out_fp)
    return True


@Stage.register(is_metadata=True, stage_name=STAGE_NAME, default_config={})
def main(cfg: DictConfig) -> None:
    """Sort the ``parent_codes`` of every code in the finished ``metadata/codes.parquet``, in place.

    A post-stage of the registered ``eICU`` spec (``etl.post_stages``), run before
    ``sumeds_enrich``. It takes no options.
    """
    if cfg.worker != 0:
        return
    codes_fp = Path(cfg.stage_cfg.metadata_input_dir) / "codes.parquet"
    out_fp = Path(cfg.stage_cfg.reducer_output_dir) / "codes.parquet"
    if sort_parent_codes(codes_fp, out_fp):
        logger.info(f"Sorted the parent codes in {out_fp.resolve()!s}.")
    else:
        logger.info(f"{codes_fp.resolve()!s} has no parent codes to sort, or is already enriched.")
