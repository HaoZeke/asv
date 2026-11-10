# Licensed under a 3-clause BSD style license - see LICENSE.rst
import os

import pytest

from asv import config, environment
from asv.repo import get_repo
from asv.util import shlex_quote as quote

from .conftest import hglib
from .tools import DUMMY1_VERSION, DUMMY2_VERSIONS, HAS_VIRTUALENV, PYTHON_VER2, generate_test_repo

pytestmark = pytest.mark.skipif(not HAS_VIRTUALENV, reason="Requires virtualenv")

DVCS_TYPES = [
    "git",
    pytest.param("hg", marks=pytest.mark.skipif(hglib is None, reason="needs hglib")),
]

# Records the version of the dummy build requirement visible to the build
# into the built package, then builds it without build isolation.
RECORD_PY = (
    "import asv_dummy_test_package_2 as m\n"
    "with open('asv_test_repo/build_req.py', 'w') as f:\n"
    "    f.write('version = %r\\n' % m.__version__)\n"
)


def _write_pyproject(path, requires):
    with open(os.path.join(path, 'pyproject.toml'), 'w') as f:
        f.write(f"[build-system]\nrequires = {requires!r}\n")


def _commit_pyproject(dvcs, project_path, requires, message):
    _write_pyproject(project_path, requires)
    dvcs.add(os.path.join(project_path, 'pyproject.toml'))
    dvcs.commit(message)
    return dvcs.get_branch_hashes()[0]


def _make_conf(tmpdir, dvcs, subdir, matrix):
    record_py = os.path.join(tmpdir, 'record_build_req.py')
    with open(record_py, 'w') as f:
        f.write(RECORD_PY)

    conf = config.Config()
    conf.env_dir = os.path.join(tmpdir, "env")
    conf.environment_type = "virtualenv"
    conf.pythons = [PYTHON_VER2]
    conf.repo = os.path.abspath(dvcs.path)
    conf.repo_subdir = subdir
    conf.matrix = matrix
    conf.build_cache_size = 0
    conf.build_command = [
        f"python {quote(record_py)}",
        "python -m pip wheel --no-build-isolation --no-deps -w {build_cache_dir} {build_dir}",
    ]
    return conf


def _built_with(env):
    return env.run(['-c', 'import asv_test_repo.build_req as b; print(b.version)']).strip()


@pytest.mark.parametrize("dvcs_type", DVCS_TYPES)
def test_build_requirements_from_commit(tmpdir, dummy_packages, monkeypatch, dvcs_type):
    tmpdir = str(tmpdir)
    old, new = DUMMY2_VERSIONS
    subdir = 'project'

    dvcs = generate_test_repo(tmpdir, [0], dvcs_type=dvcs_type, subdir=subdir)
    project_path = os.path.join(dvcs.path, subdir)
    commit_old = _commit_pyproject(
        dvcs, project_path, ["setuptools", f"asv_dummy_test_package_2=={old}"], "old"
    )
    commit_new = _commit_pyproject(
        dvcs, project_path, ["setuptools", f"asv_dummy_test_package_2 == {new}"], "new"
    )

    # The pyproject.toml in the directory asv is run from must not matter
    cwd = os.path.join(tmpdir, 'cwd')
    os.makedirs(cwd)
    _write_pyproject(
        cwd,
        [f"asv_dummy_test_package_1=={DUMMY1_VERSION}", f"asv_dummy_test_package_2=={new}"],
    )
    monkeypatch.chdir(cwd)

    conf = _make_conf(tmpdir, dvcs, subdir, matrix={})
    repo = get_repo(conf)
    env = next(iter(environment.get_environments(conf, None)))
    env.create()

    env.install_project(conf, repo, commit_old)
    assert _built_with(env) == old

    env.install_project(conf, repo, commit_new)
    assert _built_with(env) == new

    # Same environment, back to the older commit's build requirements
    env.install_project(conf, repo, commit_old)
    assert _built_with(env) == old

    env.run(
        [
            '-c',
            'import importlib.util, sys; '
            'sys.exit(importlib.util.find_spec("asv_dummy_test_package_1") is not None)',
        ]
    )


@pytest.mark.parametrize("dvcs_type", DVCS_TYPES)
def test_build_requirements_matrix_wins(tmpdir, dummy_packages, monkeypatch, dvcs_type):
    tmpdir = str(tmpdir)
    old, new = DUMMY2_VERSIONS

    dvcs = generate_test_repo(tmpdir, [0], dvcs_type=dvcs_type)
    requires = ["setuptools", f"asv_dummy_test_package_2>={new}"]
    commit = _commit_pyproject(dvcs, dvcs.path, requires, "requires new")

    # Same pyproject.toml in the directory asv is run from
    cwd = os.path.join(tmpdir, 'cwd')
    os.makedirs(cwd)
    _write_pyproject(cwd, requires)
    monkeypatch.chdir(cwd)

    conf = _make_conf(tmpdir, dvcs, '', matrix={"pip+asv-dummy-test-package-2": old})
    repo = get_repo(conf)
    env = next(iter(environment.get_environments(conf, None)))
    env.create()

    env.install_project(conf, repo, commit)
    assert _built_with(env) == old


def test_get_build_requirements(tmpdir, monkeypatch):
    tmpdir = str(tmpdir)
    conf = config.Config()
    conf.env_dir = os.path.join(tmpdir, "env")
    conf.environment_type = "virtualenv"
    conf.pythons = [PYTHON_VER2]
    conf.matrix = {"pip+Setuptools_SCM": "8.0", "cython": ""}
    env = next(iter(environment.get_environments(conf, None)))

    # No pyproject.toml, or one without build-system: nothing to install
    assert env._get_build_requirements(tmpdir) == []
    with open(os.path.join(tmpdir, 'pyproject.toml'), 'w') as f:
        f.write('[project]\nname = "x"\n')
    assert env._get_build_requirements(tmpdir) == []

    _write_pyproject(
        tmpdir,
        [
            "setuptools >= 77",
            "setuptools-scm[toml]>=6",
            "Cython>=3; python_version >= '3.9'",
            "build",
            "numpy>=2.0",
        ],
    )
    other = os.path.join(tmpdir, 'other')
    os.makedirs(other)
    _write_pyproject(other, ["flit_core"])
    monkeypatch.chdir(other)

    assert env._get_build_requirements(tmpdir) == ["setuptools >= 77", "numpy>=2.0"]
