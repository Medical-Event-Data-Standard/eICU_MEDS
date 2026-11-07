# Development

## Setup

```bash
git clone https://github.com/Medical-Event-Data-Standard/eICU_MEDS.git
cd eICU_MEDS
uv sync
uv run pre-commit install
```

`uv sync` installs the `dev` dependency group (pre-commit, ruff, pytest) by default.

## Tests

```bash
uv run pytest src/ tests/ --doctest-modules
```

`tests/e2e_demo_test.py` downloads the public eICU demo from PhysioNet, and its suMEDS enrichment
fetches ontology data from GitHub, so a first run is slow. To reuse a download, point
`EICU_MEDS_DEMO_RAW_DIR` at its directory. The other tests need no network and run in under a minute
(`tests/test_run_with_enrichment.py` runs the whole ETL and the enrichment on a few synthetic rows):

```bash
uv run pytest src/ tests/ --doctest-modules --ignore=tests/e2e_demo_test.py
```

## Running the ETL on the full release

eICU-CRD is credentialed PhysioNet data. Never commit raw files or MEDS outputs, and in issues and
PRs report aggregate numbers only.

```bash
uv run MEDS_extract-eICU root_output_dir=$OUT raw_input_dir=/path/to/eicu-crd/2.0 do_download=False
```

## Versioning and releases

The version is static in `pyproject.toml` and managed with
[`uv version`](https://docs.astral.sh/uv/reference/cli/#uv-version). A release is a git tag that
is exactly that version, with no `v` prefix (`0.2.0`, not `v0.2.0`).

Between releases, `pyproject.toml` holds the last released version, so a development install
reports that version. Before the switch to static versions, setuptools-scm reported it as a
`.devN` version instead.

### Choosing the part to bump

Until 1.0:

- **minor** (`0.1.0` → `0.2.0`): anything that changes existing output, such as codes, values,
    times, `parent_codes` or which events are emitted. Also a change to how the ETL is run, or a
    move to a new minor version of MEDS-Extract.
- **patch** (`0.1.0` → `0.1.1`): fixes and additions that leave existing codes and values as they
    were.

### Cutting a release

The bump goes through a PR like any other change to `main`. In a fork, `upstream` is
`Medical-Event-Data-Standard/eICU_MEDS`; in a direct clone, use `origin` instead.

```bash
git switch main && git pull upstream main
uv version --bump minor --dry-run       # shows the new version without changing anything
uv version --bump minor                 # or patch / major; rewrites pyproject.toml and uv.lock
VERSION=$(uv version --short)
git switch -c "release-$VERSION"
git add pyproject.toml uv.lock
git commit -m "Release $VERSION"
git push -u origin "release-$VERSION"   # then open a PR to main and merge it
```

Once the PR is merged, tag the merge commit on `main` and push only the tag:

```bash
git switch main && git pull upstream main
uv version --short                      # must print $VERSION
git tag -a "$VERSION" -m "Release $VERSION"
git push upstream "$VERSION"
```

Pushing the tag runs `.github/workflows/python-build.yaml`, which does the rest:

1. **Builds** the sdist and wheel. Before that, it checks that the tag equals the
    `pyproject.toml` version, and fails otherwise.
2. **Publishes to PyPI** through trusted publishing (the `pypi` environment).
3. **Signs** the distributions with Sigstore.
4. **Creates the GitHub release** with the signed files attached.

> [!IMPORTANT]
> Do not create the GitHub release yourself first. The workflow's `gh release create` step fails
> if the release already exists. Push only the tag, then edit the release notes on GitHub
> afterwards, since the workflow creates the release with empty notes.

If the tag check fails, nothing has been published. Delete the tag, fix the version, and tag again:

```bash
git tag -d "$VERSION" && git push upstream ":refs/tags/$VERSION"
```

## CI

| Workflow                 | Runs on                                             | Does                                                      |
| ------------------------ | --------------------------------------------------- | --------------------------------------------------------- |
| `tests.yaml`             | pushes to `main`; PRs to `main`, `dev`, `release/*` | downloads the demo once, then pytest on Python 3.11, 3.12 |
| `code-quality-pr.yaml`   | PRs to `main`, `dev`, `release/*`                   | pre-commit on the changed files                           |
| `code-quality-main.yaml` | pushes to `main`                                    | pre-commit on all files                                   |
| `python-build.yaml`      | every push                                          | builds; publishes only on tag pushes                      |
