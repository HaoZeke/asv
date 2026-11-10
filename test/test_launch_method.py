# Licensed under a 3-clause BSD style license - see LICENSE.rst

import os
import socket
import sys

import pytest

from asv import runner, util

from . import tools
from .conftest import generate_basic_conf
from .tools import get_default_environment_type

needs_unix_socket_mark = pytest.mark.skipif(
    not (hasattr(os, 'fork') and hasattr(socket, 'AF_UNIX')),
    reason="test requires fork and unix sockets",
)


@pytest.fixture
def spawner_calls(monkeypatch):
    calls = []
    orig = runner.get_spawner

    def get_spawner(env, benchmark_dir, launch_method):
        calls.append(launch_method)
        return orig(env, benchmark_dir, launch_method=launch_method)

    monkeypatch.setattr(runner, "get_spawner", get_spawner)
    return calls


def _venv_spec(conf):
    python = f"{sys.version_info[0]}.{sys.version_info[1]}"
    return ["-E", get_default_environment_type(conf, python) + ":" + python]


def _run_command(command, conf, machine_file, extra_args):
    branch = util.git_default_branch()
    if command == "run":
        argv = ["run", "--quick", "--python=same", "--bench=time_secondary.track_value"]
    elif command == "continuous":
        argv = [
            "continuous",
            f"{branch}^",
            branch,
            "--quick",
            "--bench=time_secondary.track_value",
            *_venv_spec(conf),
        ]
    elif command == "find":
        argv = ["find", f"{branch}~5..{branch}", "time_secondary.track_value", *_venv_spec(conf)]
    elif command == "profile":
        argv = ["profile", "--python=same", "time_secondary.track_value"]
    else:
        raise AssertionError(command)
    tools.run_asv_with_conf(conf, *argv, *extra_args, _machine_file=machine_file)


COMMANDS = ["run", "continuous", "find", "profile"]


@needs_unix_socket_mark
@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize(
    "conf_value, extra_args, expected",
    [
        (None, [], "auto"),
        ("forkserver", [], "forkserver"),
        ("forkserver", ["--launch-method=spawn"], "spawn"),
        ("spawn", ["--launch-method=forkserver"], "forkserver"),
    ],
    ids=["default", "config", "flag-overrides-config", "flag-overrides-config-2"],
)
def test_command_launch_method(tmpdir, spawner_calls, command, conf_value, extra_args, expected):
    values = [(None, None), (1, 1), (3, 1), (None, 1), (6, None), (5, 1), (6, 1)]
    tmpdir, local, conf, machine_file = generate_basic_conf(
        tmpdir, values=values, dummy_packages=False
    )
    conf.launch_method = conf_value

    _run_command(command, conf, machine_file, extra_args)

    # get_spawner treats None as "auto"
    assert spawner_calls
    assert {"auto" if c is None else c for c in spawner_calls} == {expected}
