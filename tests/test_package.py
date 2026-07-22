"""Tests for the korvex package itself, as opposed to any one of its APIs."""

from importlib.metadata import version

import korvex


def test_dunder_version_matches_installed_metadata():
    """Regression test: __version__ was once a hand-written string in
    __init__.py that silently drifted from the version pip/PyPI actually
    report for the installed package (0.0.1 vs the real 0.1.0)."""
    assert korvex.__version__ == version("korvex")
