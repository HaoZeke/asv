# Licensed under a 3-clause BSD style license - see LICENSE.rst

try:
    from importlib.metadata import version as get_version
except ImportError:  # pragma: no cover
    from importlib_metadata import version as get_version
