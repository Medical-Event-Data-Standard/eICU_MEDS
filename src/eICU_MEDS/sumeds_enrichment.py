"""Optional suMEDS enrichment of the finished ``metadata/codes.parquet`` (``do_enrich=True``).

The ETL writes what eICU itself records. With the packaged ``sumeds_rules.yaml``, suMEDS adds what
eICU does not: LOINC parents for lab codes and parents among eICU's own path codes (see the README).
It is off by default because suMEDS is an optional dependency (``pip install 'eICU-MEDS[sumeds]'``)
and fetches its pinned ontology data from GitHub on first use, unless ``SUMEDS_ONTOLOGY_SOURCE``
points at a local copy.
"""

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from .code_descriptions import write_code_metadata

if TYPE_CHECKING:  # pragma: no cover
    from sumeds import EnrichmentConfig

SUMEDS_RULES = files("eICU_MEDS") / "sumeds_rules.yaml"


def sumeds_config() -> "EnrichmentConfig":
    """The suMEDS enrichment config for the packaged rules.

    Built before the ETL runs, so that a missing suMEDS or a rules file it rejects fails at once
    rather than after a full extraction.
    """
    try:
        from sumeds import EnrichmentConfig
    except ImportError as e:
        raise ImportError("do_enrich=True needs suMEDS: pip install 'eICU-MEDS[sumeds]'") from e
    return EnrichmentConfig(rules=Path(str(SUMEDS_RULES)))


def _n_parent_links(codes: pl.DataFrame) -> int:
    return int(codes["parent_codes"].list.len().fill_null(0).sum())


def enrich_code_metadata(codes_fp: Path, config: "EnrichmentConfig | None" = None) -> tuple[int, int]:
    """Enrich ``codes_fp`` in place with suMEDS.

    suMEDS only adds parents and columns; it never removes or rewrites the ETL's own parents or
    descriptions. The file is replaced atomically, so a failed enrichment (e.g. no network for the
    ontology data) leaves the ETL's ``codes.parquet`` as it was.

    Args:
        codes_fp: The ``metadata/codes.parquet`` of a finished extraction.
        config: Override of :func:`sumeds_config`, for tests.

    Returns:
        The number of parent links before and after enrichment.
    """
    from sumeds import enrich_metadata

    if config is None:
        config = sumeds_config()
    codes = pl.read_parquet(codes_fp)
    enriched = enrich_metadata(codes.lazy(), config).collect()
    write_code_metadata(enriched, codes_fp)
    return _n_parent_links(codes), _n_parent_links(enriched)
