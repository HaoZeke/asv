# Licensed under a 3-clause BSD style license - see LICENSE.rst

import json
import os
import shutil
import site
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# The commands below are the run steps from
# docs/source/examples/benchmark-pull-request.yml, with the pull-request
# SHAs filled in. The benchmark repo plants a 3% slowdown in one function
# and leaves the other unchanged.


BURN_C = r"""
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>

__attribute__((noinline)) static uint64_t burn(uint64_t n) {
    uint64_t sum = 0;
    uint64_t i;
    for (i = 0; i < n; i++)
        sum += i * 3u + 1u;
    return sum;
}

static PyObject *changed(PyObject *self, PyObject *arg) {
    unsigned long long n = PyLong_AsUnsignedLongLong(arg);
    if (PyErr_Occurred())
        return NULL;
    EXTRA
    return PyLong_FromUnsignedLongLong(burn(n));
}

static PyObject *same(PyObject *self, PyObject *arg) {
    unsigned long long n = PyLong_AsUnsignedLongLong(arg);
    if (PyErr_Occurred())
        return NULL;
    return PyLong_FromUnsignedLongLong(burn(n));
}

static PyMethodDef methods[] = {
    {"changed", changed, METH_O, NULL},
    {"same", same, METH_O, NULL},
    {NULL, NULL, 0, NULL},
};
static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "burn", NULL, -1, methods};
PyMODINIT_FUNC PyInit_burn(void) { return PyModule_Create(&module); }
"""

BENCH_PY = textwrap.dedent(
    """\
    import burn

    COUNT = 8_000_000


    def time_slow():
        burn.changed(COUNT)


    def time_same():
        burn.same(COUNT)


    time_slow.repeat = 5
    time_slow.number = 1
    time_slow.warmup_time = 0
    time_same.repeat = 5
    time_same.number = 1
    time_same.warmup_time = 0
    """
)

SETUP_PY = textwrap.dedent(
    """\
    from setuptools import Extension, setup

    setup(
        name="burn",
        version="0.1.0",
        ext_modules=[Extension("burn", ["burn.c"])],
    )
    """
)


def _git(repo, *args):
    subprocess.check_call(["git", *args], cwd=repo)


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message, "--no-gpg-sign")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def _write_tree(repo, extra):
    (repo / "burn.c").write_text(BURN_C.replace("EXTRA", extra))
    (repo / "setup.py").write_text(SETUP_PY)
    bench = repo / "benchmarks"
    bench.mkdir(exist_ok=True)
    (bench / "__init__.py").write_text("")
    (bench / "bench.py").write_text(BENCH_PY)


def _job_summary(repo, home, base, head):
    """Run the workflow's asv commands and return the compare markdown."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    # The checkout under test and the already-installed dependencies stay
    # importable when HOME points at the temporary machine-file directory.
    repo_root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root), site.getusersitepackages(), env.get("PYTHONPATH", "")]
    )
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")

    def asv(*args):
        return subprocess.run(
            [sys.executable, "-m", "asv", *args],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
        )

    machine = asv("machine", "--machine", "ci", "--yes")
    assert machine.returncode == 0, machine.stderr
    # These arguments match docs/source/examples/benchmark-pull-request.yml.
    continuous = asv(
        "continuous",
        base,
        head,
        "--machine",
        "ci",
        "--interleave-rounds",
        "20",
        "--min-rounds",
        "6",
        "--aa-band",
    )
    # A detected slowdown exits 1. A benchmark failure exits 2.
    assert continuous.returncode in (0, 1), continuous.stdout + "\n" + continuous.stderr
    compared = asv("compare", base, head, "--machine", "ci")
    assert compared.returncode == 0, compared.stdout + "\n" + continuous.stdout
    return compared.stdout, continuous.stdout + continuous.stderr


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs a C compiler")
def test_pull_request_summary_flags_only_the_slowdown(tmpdir):
    root = Path(str(tmpdir))
    repo = root / "repo"
    home = root / "home"
    repo.mkdir()
    home.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "robot@asv")
    _git(repo, "config", "user.name", "Robotic Swallow")
    py = f"{sys.version_info[0]}.{sys.version_info[1]}"
    conf = {
        "version": 1,
        "project": "burn",
        "project_url": "https://example.com/burn",
        "repo": str(repo),
        "branches": ["main"],
        "dvcs": "git",
        "environment_type": "virtualenv",
        "pythons": [py],
        "matrix": {},
        "benchmark_dir": "benchmarks",
        "env_dir": str(root / "env"),
        "results_dir": str(root / "results"),
        "html_dir": str(root / "html"),
    }
    (repo / "asv.conf.json").write_text(json.dumps(conf))
    _write_tree(repo, "")
    base = _commit(repo, "base")
    _write_tree(repo, "n += n / 33;")
    head = _commit(repo, "slow the changed loop by about 3 percent")
    bare = root / "origin.git"
    subprocess.check_call(["git", "clone", "--bare", str(repo), str(bare)])
    _git(repo, "remote", "add", "origin", str(bare))

    summary, log = _job_summary(repo, home, base, head)
    slow = [line for line in summary.splitlines() if "time_slow" in line]
    same = [line for line in summary.splitlines() if "time_same" in line]
    assert len(slow) == 1, summary
    assert len(same) == 1, summary
    assert slow[0].split("|")[1].strip() == "+", summary
    assert same[0].split("|")[1].strip() == "", summary
    assert "1.03" in slow[0]
