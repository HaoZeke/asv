# Licensed under a 3-clause BSD style license - see LICENSE.rst

import os
import re
import sys

from asv import util
from asv.results import Results, iter_results_for_machine

from . import tools
from .tools import get_default_environment_type


def test_continuous(capfd, basic_conf_2):
    tmpdir, local, conf, machine_file = basic_conf_2

    python = f"{sys.version_info[0]}.{sys.version_info[1]}"
    env_type = get_default_environment_type(conf, python)
    env_spec = ("-E", env_type + ":" + python)

    # Check that asv continuous runs
    tools.run_asv_with_conf(
        conf,
        'continuous',
        f"{util.git_default_branch()}^",
        '--show-stderr',
        '--bench=params_examples.track_find_test',
        '--bench=params_examples.track_param',
        '--bench=time_examples.TimeSuite.time_example_benchmark_1',
        '--attribute=repeat=1',
        '--attribute=number=1',
        '--attribute=warmup_time=0',
        *env_spec,
        _machine_file=machine_file,
    )

    text, err = capfd.readouterr()
    assert "SOME BENCHMARKS HAVE CHANGED SIGNIFICANTLY" in text
    assert "PERFORMANCE INCREASED" in text or "PERFORMANCE DECREASED" in text
    # Check output, but not the whole row
    pattern = r"params_examples\.track_find_test\(2\) \[orangutan\/\w+-py"
    assert re.search(pattern, text) is not None

    assert "params_examples.ClassOne" in text

    # Check rounds were interleaved (timing benchmark was run twice)
    assert re.search(
        r"For.*commit [a-f0-9]+ (<[a-z0-9~^]+> )?\(round 1/2\)", text, re.MULTILINE
    ), text

    result_found = False
    for results in iter_results_for_machine(conf.results_dir, "orangutan"):
        result_found = True
        stats = results.get_result_stats('time_examples.TimeSuite.time_example_benchmark_1', [])
        assert stats[0]['repeat'] == 2
    assert result_found


def test_interleave_multi_env_bench_and_failed_round(capfd, basic_conf_2):
    tmpdir, _local, conf, machine_file = basic_conf_2
    conf.matrix = {"env": {"ASV_PROBE_ENV": ["a", "b"]}}
    fail_count = os.path.join(str(tmpdir), 'fail-count.txt')
    os.environ['ASV_FAIL_COUNT'] = fail_count
    probe = os.path.join(str(tmpdir), 'benchmark', 'time_interleave_probe.py')
    with open(probe, 'w') as handle:
        handle.write(
            "import os\n"
            "\n"
            "def _burn():\n"
            "    total = 0\n"
            "    for i in range(200):\n"
            "        total += i\n"
            "    return total\n"
            "\n"
            "def time_ok():\n"
            "    _burn()\n"
            "\n"
            "def time_fail_once():\n"
            "    path = os.environ['ASV_FAIL_COUNT']\n"
            "    count = 0\n"
            "    if os.path.exists(path):\n"
            "        with open(path) as handle:\n"
            "            text = handle.read().strip()\n"
            "            if text:\n"
            "                count = int(text)\n"
            "    count += 1\n"
            "    with open(path, 'w') as handle:\n"
            "        handle.write(str(count))\n"
            "    if count == 2:\n"
            "        raise RuntimeError('planned failure')\n"
            "    _burn()\n"
            "\n"
            "def time_not_selected():\n"
            "    _burn()\n"
        )
    try:
        status = tools.run_asv_with_conf(
            conf,
            'continuous',
            '--interleave-rounds',
            '2',
            '--aa-band',
            '--bench=time_interleave_probe.time_ok',
            '--bench=time_interleave_probe.time_fail_once',
            '--attribute=repeat=1',
            '--attribute=number=1',
            '--attribute=warmup_time=0',
            '--no-only-changed',
            f"{util.git_default_branch()}^",
            _machine_file=machine_file,
        )
    finally:
        os.environ.pop('ASV_FAIL_COUNT', None)

    text, err = capfd.readouterr()
    combined = text + err
    assert 'failed in round' in combined
    assert 'time_interleave_probe.time_ok' in text
    assert 'time_interleave_probe.time_fail_once' in text
    assert 'time_not_selected' not in combined
    assert 'BENCHMARKS' in text
    assert status == 2

    result_dir = os.path.join(conf.results_dir, 'orangutan')
    loaded = []
    env_names = set()
    for filename in os.listdir(result_dir):
        if not filename.endswith('.json') or filename == 'machine.json':
            continue
        env_name = filename.split('-', 1)[1][: -len('.json')]
        env_names.add(env_name.removesuffix('~aa'))
        loaded.append(Results.load(os.path.join(result_dir, filename), 'orangutan'))
    assert len(env_names) == 2
    assert any(name.endswith('ASV_PROBE_ENVa') for name in env_names)
    assert any(name.endswith('ASV_PROBE_ENVb') for name in env_names)

    failed = []
    compared = None
    for result in loaded:
        keys = set(result.get_all_result_keys())
        assert 'time_interleave_probe.time_not_selected' not in keys
        ok_name = 'time_interleave_probe.time_ok'
        assert ok_name in keys
        rounds = result.round_samples[ok_name]
        assert len(rounds) == 2
        assert all(group is not None for group in rounds)
        fail_name = 'time_interleave_probe.time_fail_once'
        if fail_name in result.failed_rounds:
            failed.append(result.failed_rounds[fail_name])
        if result._comparisons:
            compared = result
    assert failed
    assert compared is not None
    parent = next(iter(compared._comparisons))
    payload = compared.get_comparison(parent)
    assert 'time_interleave_probe.time_ok' in payload['ratio_ci']
    assert 'time_interleave_probe.time_ok' in payload['aa_band']

    tools.run_asv_with_conf(
        conf,
        'compare',
        parent,
        compared.commit_hash,
        '--machine=orangutan',
        '--format=latex',
        '--columns=before,after,ci,aa,benchmark',
        '--no-only-changed',
    )
    text, err = capfd.readouterr()
    assert 'Ratio CI' in text
    assert 'A/A' in text
    assert r'\num{' in text
    assert r'time\_interleave\_probe.time\_ok' in text
    assert 'time_not_selected' not in text
    assert r'ASV\_PROBE\_ENVa' in text
    assert r'ASV\_PROBE\_ENVb' in text
