# eICU MEDS Extraction ETL

[![PyPI - Version](https://img.shields.io/pypi/v/ETL-MEDS)](https://pypi.org/project/ETL-MEDS/)
[![Documentation Status](https://readthedocs.org/projects/etl-meds/badge/?version=latest)](https://etl-meds.readthedocs.io/en/stable/?badge=stable)
![Static Badge](https://img.shields.io/badge/MEDS-0.3.3-blue)

[![codecov](https://codecov.io/gh/Medical-Event-Data-Standard/ETL_MEDS_Template/graph/badge.svg?token=RW6JXHNT0W)](https://codecov.io/gh/Medical-Event-Data-Standard/ETL_MEDS_Template)
[![tests](https://github.com/Medical-Event-Data-Standard/ETL_MEDS_Template/actions/workflows/tests.yaml/badge.svg)](https://github.com/Medical-Event-Data-Standard/ETL_MEDS_Template/actions/workflows/tests.yml)
[![code-quality](https://github.com/Medical-Event-Data-Standard/ETL_MEDS_Template/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/Medical-Event-Data-Standard/ETL_MEDS_Template/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/-Python_3.11-blue?logo=python&logoColor=white)
[![license](https://img.shields.io/badge/License-MIT-green.svg?labelColor=gray)](https://github.com/Medical-Event-Data-Standard/ETL_MEDS_Template#license)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/Medical-Event-Data-Standard/ETL_MEDS_Template/pulls)
[![contributors](https://img.shields.io/github/contributors/Medical-Event-Data-Standard/ETL_MEDS_Template.svg)](https://github.com/Medical-Event-Data-Standard/ETL_MEDS_Template/graphs/contributors)

A template repository for a MEDS-Transforms powered extraction pipeline for a custom dataset. Once you have
customized the repository to your dataset (see instructions below), you will be able to run your extraction
pipeline with a few simple command-line commands, such as:

```bash
pip install -e . # using editing mode
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
