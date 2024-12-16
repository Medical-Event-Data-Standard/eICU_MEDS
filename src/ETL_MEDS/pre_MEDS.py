#!/usr/bin/env python

"""Performs pre-MEDS data wrangling for MIMIC-IV."""


import hydra
from omegaconf import DictConfig

from . import PRE_MEDS_CFG


@hydra.main(version_base=None, config_path=str(PRE_MEDS_CFG.parent), config_name=PRE_MEDS_CFG.stem)
def main(cfg: DictConfig):
    """Performs pre-MEDS data wrangling for INSERT DATASET NAME HERE."""

    raise NotImplementedError("Please implement the pre_MEDS.py script for your dataset.")


if __name__ == "__main__":
    main()
