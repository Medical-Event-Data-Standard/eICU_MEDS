"""Optional suMEDS enrichment of the finished ``metadata/codes.parquet`` (``do_enrich=True``).

The ETL writes what eICU itself records. With the packaged ``sumeds_rules.yaml``, suMEDS adds what
eICU does not: LOINC parents for lab codes and parents among eICU's own path codes (see the README).
suMEDS enriches the file in place itself; this module only supplies the eICU side: the rules and the
install hint. Enrichment is off by default because suMEDS is an optional dependency
(``pip install 'eICU-MEDS[sumeds]'``) and fetches its pinned ontology data from GitHub on first use,
unless ``SUMEDS_ONTOLOGY_SOURCE`` points at a local copy.
"""

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

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
