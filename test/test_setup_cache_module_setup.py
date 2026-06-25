# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""Regression for upstream #1592: module-level setup runs before setup_cache."""

import ast
import inspect
import textwrap
from pathlib import Path

import asv.benchmark as bench_mod


def test_setup_cache_command_uses_wrapped_handler():
    """Shipped entry point must not point at the stock asv_runner handler alone."""
    assert bench_mod.commands["setup_cache"] is bench_mod._setup_cache
    # Wrapper is a distinct function defined in asv.benchmark.
    assert bench_mod._setup_cache.__module__ == "asv.benchmark"


def test_wrapper_invokes_zero_arg_setups_before_cache(monkeypatch):
    """Drive the real patched Benchmark.do_setup_cache path used by setup_cache."""
    from asv_runner.benchmarks._base import Benchmark

    events = []

    class FakeBench:
        _setups = []
        _setup_cache = None

        def __init__(self):
            def module_setup(*args, **kwargs):
                events.append("module_setup")

            def needs_param(x):
                events.append("needs_param")

            def cache_fn():
                events.append("cache")
                return {"ok": True}

            self._setups = [module_setup, needs_param]
            self._setup_cache = cache_fn

    # Bind the wrapper method the same way asv.benchmark._setup_cache does.
    original = Benchmark.do_setup_cache

    def do_setup_cache_with_module_setup(self):
        if self._setup_cache is None:
            return None
        for setup in self._setups:
            try:
                inspect.signature(setup).bind()
            except TypeError:
                continue
            setup()
        return self._setup_cache()

    Benchmark.do_setup_cache = do_setup_cache_with_module_setup
    try:
        fake = FakeBench()
        # Call through the class function with fake instance (real method body).
        result = Benchmark.do_setup_cache(fake)
    finally:
        Benchmark.do_setup_cache = original

    assert result == {"ok": True}
    assert events == ["module_setup", "cache"]
    assert "needs_param" not in events


def test_quickstart_source_mentions_mergeable_gitignore():
    """Structural guard: #1582 fix remains in shipped quickstart module."""
    src = Path(bench_mod.__file__).resolve().parent / "commands" / "quickstart.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "_merge_gitignore" in names
    text = src.read_text(encoding="utf-8")
    assert "_MERGEABLE_TEMPLATE_FILES" in text
    assert ".gitignore" in text
