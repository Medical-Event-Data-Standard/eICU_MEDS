# eICU MEDS Extraction ETL

[![PyPI - Version](https://img.shields.io/pypi/v/eICU-MEDS)](https://pypi.org/project/eICU-MEDS/)
[![Documentation Status](https://readthedocs.org/projects/etl-meds/badge/?version=latest)](https://etl-meds.readthedocs.io/en/stable/?badge=stable)
![Static Badge](https://img.shields.io/badge/MEDS-0.3.3-blue)
[![codecov](https://codecov.io/gh/Medical-Event-Data-Standard/eICU_MEDS/graph/badge.svg?token=RW6JXHNT0W)](https://codecov.io/gh/Medical-Event-Data-Standard/eICU_MEDS)
[![tests](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/actions/workflows/tests.yaml/badge.svg)](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/actions/workflows/tests.yml)
[![code-quality](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/-Python_3.11-blue?logo=python&logoColor=white)
[![license](https://img.shields.io/badge/License-MIT-green.svg?labelColor=gray)](https://github.com/Medical-Event-Data-Standard/eICU_MEDS#license)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/pulls)
[![contributors](https://img.shields.io/github/contributors/Medical-Event-Data-Standard/eICU_MEDS.svg)](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/graphs/contributors)

This repository contains the code for downloading the
[eICU dataset](https://physionet.org/content/eicu-crd/2.0/) from PhysioNet and transforming it into the
[Medical Event Data Standard (MEDS)](https://medical-event-data-standard.org/) format.

```bash
pip install eICU-MEDS # use `pip install -e .` for local installation in editing mode
export DATASET_DOWNLOAD_USERNAME=$PHYSIONET_USERNAME
export DATASET_DOWNLOAD_PASSWORD=$PHYSIONET_PASSWORD
MEDS_extract-eICU root_output_dir=data/eicu_meds do_download=False
```

## MEDS-transforms settings

If you want to convert a large dataset, you can use parallelization with MEDS-transforms
(the MEDS-transformation step that takes the longest).

Using local parallelization with the `hydra-joblib-launcher` package, you can set the number of workers:

```
pip install hydra-joblib-launcher --upgrade
```

Then, you can set the number of workers as environment variable:

```bash
export N_WORKERS=8
```

Moreover, you can set the number of subjects per shard to balance the parallelization overhead based on how many
subjects you have in your dataset:

```bash
export N_SUBJECTS_PER_SHARD=100000
```
