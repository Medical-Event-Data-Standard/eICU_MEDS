"""eICU-MEDS: the eICU-CRD ETL to MEDS, as a MEDS-Extract spec registered under the name ``eICU``.

Run it with ``meds-extract-run spec=eICU output_dir=OUT``. The package ships the spec
(``configs/event_configs.yaml``), the suMEDS rules (``sumeds_rules.yaml``) and two MEDS-Extract
extension stages (``pre_MEDS.py`` and ``sort_parent_codes.py``).
"""

from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files

__package_name__ = "eICU_MEDS"
try:
    __version__ = version(__package_name__)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"

EVENT_CFG = files(__package_name__).joinpath("configs/event_configs.yaml")

__all__ = ["EVENT_CFG", "__package_name__", "__version__"]
