# Installation

muscadet requires Python 3.10. The upper bound comes from PyCATSHOO.

## PyCATSHOO

PyCATSHOO is not distributed on PyPI. Install it by following the instructions
on [pycatshoo.org](http://www.pycatshoo.org/), then check the installation:

```python
import Pycatshoo
```

## muscadet

```sh
pip install git+https://github.com/edgemind-sas/muscadet.git
```

This also installs [cod3s](https://github.com/edgemind-sas/cod3s), the
component and system framework muscadet builds on, at the version the release
pins.

## From a checkout

```sh
git clone https://github.com/edgemind-sas/muscadet.git
cd muscadet
uv sync
source .venv/bin/activate
pytest
```

To build this site locally:

```sh
uv sync --group docs
mkdocs serve
```
