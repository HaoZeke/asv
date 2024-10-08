import re

import pytest

from asv import util

from . import tools


def test_profile_python_same(capsys, basic_conf):
    tmpdir, local, conf, machine_file = basic_conf

    # Test Profile can run with python=same
    tools.run_asv_with_conf(conf, 'profile', '--python=same', "time_secondary.track_value",
                            _machine_file=machine_file)
    text, err = capsys.readouterr()

    # time_with_warnings failure case
    assert re.search(r"^\s+1\s+.*time_secondary.*\(track_value\)", text, re.M)

    # Check that it did not clone or install
    assert "Cloning" not in text
    assert "Installing" not in text


def test_profile_python_commit(capsys, basic_conf):

    tmpdir, local, conf, machine_file = basic_conf

    # Create initial results with no profile results
    tools.run_asv_with_conf(conf, 'run', "--quick", "--bench=time_secondary.track_value",
                            f'{util.git_default_branch()}^!', _machine_file=machine_file)
    text, err = capsys.readouterr()

    assert "Installing" in text

    # Query the previous empty results results; there should be no issues here
    if not util.ON_PYPY:
        tools.run_asv_with_conf(conf, 'profile', "time_secondary.track_value",
                                f'{util.git_default_branch()}', _machine_file=machine_file)
        text, err = capsys.readouterr()

        assert "Profile data does not already exist" in text

        tools.run_asv_with_conf(conf, 'run', "--quick", "--profile",
                                "--bench=time_secondary.track_value",
                                f'{util.git_default_branch()}^!', _machine_file=machine_file)
    else:
        # The ASV main process doesn't use PyPy
        with pytest.raises(util.UserError):
            tools.run_asv_with_conf(conf, 'profile', "time_secondary.track_value",
                                    f'{util.git_default_branch()}', _machine_file=machine_file)

    # Profile results should be present now
    if not util.ON_PYPY:
        tools.run_asv_with_conf(conf, 'profile', "time_secondary.track_value",
                                f'{util.git_default_branch()}', _machine_file=machine_file)
        text, err = capsys.readouterr()

        assert "Profile data does not already exist" not in text
    else:
        # The ASV main process doesn't use PyPy
        with pytest.raises(util.UserError):
            tools.run_asv_with_conf(conf, 'profile', "time_secondary.track_value",
                                    f'{util.git_default_branch()}', _machine_file=machine_file)
