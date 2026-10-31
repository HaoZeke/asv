# Licensed under a 3-clause BSD style license - see LICENSE.rst
import os
import re
import shutil
from os.path import abspath, dirname, join

import pytest

from asv import _stats, config, util
from asv.commands.compare import Compare, _format_param_for_display, unroll_result

from . import tools

try:
    import hglib
except ImportError:
    hglib = None

from .tools import WIN

MACHINE_FILE = abspath(join(dirname(__file__), 'asv-machine.json'))


def test_format_param_for_display_collapses_whitespace():
    """Regression for #1393: multiline parameter repr must not break markdown tables."""
    multiline = "AnnData(\n    obs: 'a'\n    var: 'b'\n)"
    assert '\n' not in _format_param_for_display(multiline)
    assert _format_param_for_display(multiline) == "AnnData( obs: 'a' var: 'b' )"

    names = list(
        n
        for n, _ in unroll_result(
            'suite.bench',
            [[multiline]],
            [1.0],
        )
    )
    assert len(names) == 1
    assert '\n' not in names[0]
    assert names[0].startswith('suite.bench(')


REFERENCE = """
All benchmarks:

| Change   | Before [22b920c6]    | After [fcf8c079]    | Ratio   | Benchmark (Parameter)                         |
|----------|----------------------|---------------------|---------|-----------------------------------------------|
| !        | n/a                  | failed              | n/a     | params_examples.ParamSuite.track_value        |
|          | failed               | failed              | n/a     | time_AAA_failure                              |
|          | n/a                  | n/a                 | n/a     | time_AAA_skip                                 |
|          | 1.00±1s              | 3.00±1s             | ~3.00?  | time_ci_big                                   |
| +        | 1.00±0s              | 3.00±0s             | 3.00?   | time_ci_small                                 |
| !        | 454μs                | failed              | n/a     | time_coordinates.time_latitude                |
|          | 1.00s                | 1.00s               | 1.00    | time_other.time_parameterized(1)              |
|          | 2.00s                | 4.00s               | 2.00    | time_other.time_parameterized(2)              |
| !        | 3.00s                | failed              | n/a     | time_other.time_parameterized(3)              |
| +        | 1.75ms               | 153ms               | 87.28   | time_quantity.time_quantity_array_conversion  |
| +        | 934μs                | 108ms               | 115.90  | time_quantity.time_quantity_init_array        |
|          | 83.6μs               | 55.4μs              | 0.66    | time_quantity.time_quantity_init_scalar       |
|          | 282μs                | 147μs               | 0.52    | time_quantity.time_quantity_scalar_conversion |
| +        | 1.31ms               | 7.75ms              | 5.91    | time_quantity.time_quantity_ufunc_sin         |
|          | 42                   | 42                  | 1.00    | time_secondary.track_value                    |
|          | 5.73m                | 5.73m               | 1.00    | time_units.mem_unit                           |
| +        | 125μs                | 3.81ms              | 30.42   | time_units.time_simple_unit_parse             |
|          | 1.64ms               | 1.53ms              | 0.93    | time_units.time_unit_compose                  |
| +        | 372μs                | 11.5ms              | 30.81   | time_units.time_unit_parse                    |
| -        | 69.1μs               | 18.3μs              | 0.27    | time_units.time_unit_to                       |
|          | 11.9μs               | 13.1μs              | 1.10    | time_units.time_very_simple_unit_parse        |
| +        | 1.00s                | 3.00s               | 3.00    | time_with_version_match                       |
| +        | 1.00s                | 3.00s               | 3.00    | time_with_version_mismatch_bench              |
| x        | 1.00s                | 3.00s               | 3.00    | time_with_version_mismatch_other              |

?: 2 result(s) had too few recorded samples for the significance test (needs e.g. 7 per side); run with --record-samples and more rounds/repeat, not --quick.
"""

REFERENCE_SPLIT = """
Benchmarks that have improved:

| Change   | Before [22b920c6]    | After [fcf8c079]    |   Ratio | Benchmark (Parameter)   |
|----------|----------------------|---------------------|---------|-------------------------|
| -        | 69.1μs               | 18.3μs              |    0.27 | time_units.time_unit_to |

Benchmarks that have stayed the same:

| Change   | Before [22b920c6]    | After [fcf8c079]    | Ratio   | Benchmark (Parameter)                         |
|----------|----------------------|---------------------|---------|-----------------------------------------------|
|          | failed               | failed              | n/a     | time_AAA_failure                              |
|          | n/a                  | n/a                 | n/a     | time_AAA_skip                                 |
|          | 1.00±1s              | 3.00±1s             | ~3.00?  | time_ci_big                                   |
|          | 1.00s                | 1.00s               | 1.00    | time_other.time_parameterized(1)              |
|          | 2.00s                | 4.00s               | 2.00    | time_other.time_parameterized(2)              |
|          | 83.6μs               | 55.4μs              | 0.66    | time_quantity.time_quantity_init_scalar       |
|          | 282μs                | 147μs               | 0.52    | time_quantity.time_quantity_scalar_conversion |
|          | 42                   | 42                  | 1.00    | time_secondary.track_value                    |
|          | 5.73m                | 5.73m               | 1.00    | time_units.mem_unit                           |
|          | 1.64ms               | 1.53ms              | 0.93    | time_units.time_unit_compose                  |
|          | 11.9μs               | 13.1μs              | 1.10    | time_units.time_very_simple_unit_parse        |

Benchmarks that have got worse:

| Change   | Before [22b920c6]    | After [fcf8c079]    | Ratio   | Benchmark (Parameter)                        |
|----------|----------------------|---------------------|---------|----------------------------------------------|
| !        | n/a                  | failed              | n/a     | params_examples.ParamSuite.track_value       |
| +        | 1.00±0s              | 3.00±0s             | 3.00?   | time_ci_small                                |
| !        | 454μs                | failed              | n/a     | time_coordinates.time_latitude               |
| !        | 3.00s                | failed              | n/a     | time_other.time_parameterized(3)             |
| +        | 1.75ms               | 153ms               | 87.28   | time_quantity.time_quantity_array_conversion |
| +        | 934μs                | 108ms               | 115.90  | time_quantity.time_quantity_init_array       |
| +        | 1.31ms               | 7.75ms              | 5.91    | time_quantity.time_quantity_ufunc_sin        |
| +        | 125μs                | 3.81ms              | 30.42   | time_units.time_simple_unit_parse            |
| +        | 372μs                | 11.5ms              | 30.81   | time_units.time_unit_parse                   |
| +        | 1.00s                | 3.00s               | 3.00    | time_with_version_match                      |
| +        | 1.00s                | 3.00s               | 3.00    | time_with_version_mismatch_bench             |

Benchmarks that are not comparable:

| Change   | Before [22b920c6]    | After [fcf8c079]    |   Ratio | Benchmark (Parameter)            |
|----------|----------------------|---------------------|---------|----------------------------------|
| x        | 1.00s                | 3.00s               |       3 | time_with_version_mismatch_other |

?: 2 result(s) had too few recorded samples for the significance test (needs e.g. 7 per side); run with --record-samples and more rounds/repeat, not --quick.
"""

REFERENCE_ONLY_CHANGED = """
| Change   | Before [22b920c6] <name1>   | After [fcf8c079] <name2>   | Ratio   | Benchmark (Parameter)                        |
|----------|-----------------------------|----------------------------|---------|----------------------------------------------|
| !        | n/a                         | failed                     | n/a     | params_examples.ParamSuite.track_value       |
| !        | 454μs                       | failed                     | n/a     | time_coordinates.time_latitude               |
| !        | 3.00s                       | failed                     | n/a     | time_other.time_parameterized(3)             |
| +        | 1.75ms                      | 153ms                      | 87.28   | time_quantity.time_quantity_array_conversion |
| +        | 1.31ms                      | 7.75ms                     | 5.91    | time_quantity.time_quantity_ufunc_sin        |
| +        | 372μs                       | 11.5ms                     | 30.81   | time_units.time_unit_parse                   |
| +        | 125μs                       | 3.81ms                     | 30.42   | time_units.time_simple_unit_parse            |
| +        | 1.00±0s                     | 3.00±0s                    | 3.00?   | time_ci_small                                |
| +        | 1.00s                       | 3.00s                      | 3.00    | time_with_version_match                      |
| +        | 1.00s                       | 3.00s                      | 3.00    | time_with_version_mismatch_bench             |
| +        | 934μs                       | 108ms                      | 115.90  | time_quantity.time_quantity_init_array       |
| -        | 69.1μs                      | 18.3μs                     | 0.27    | time_units.time_unit_to                      |

?: 1 result(s) had too few recorded samples for the significance test (needs e.g. 7 per side); run with --record-samples and more rounds/repeat, not --quick.
"""

REFERENCE_ONLY_CHANGED_MULTIENV = """
| Change   | Before [22b920c6]    | After [fcf8c079]    | Ratio   | Benchmark (Parameter)                                                 |
|----------|----------------------|---------------------|---------|-----------------------------------------------------------------------|
| !        | n/a                  | failed              | n/a     | params_examples.ParamSuite.track_value [cheetah/py2.7-numpy1.8]       |
| !        | 454μs                | failed              | n/a     | time_coordinates.time_latitude [cheetah/py2.7-numpy1.8]               |
| !        | 3.00s                | failed              | n/a     | time_other.time_parameterized(3) [cheetah/py2.7-numpy1.8]             |
| +        | 1.75ms               | 153ms               | 87.28   | time_quantity.time_quantity_array_conversion [cheetah/py2.7-numpy1.8] |
| +        | 1.31ms               | 7.75ms              | 5.91    | time_quantity.time_quantity_ufunc_sin [cheetah/py2.7-numpy1.8]        |
| +        | 372μs                | 11.5ms              | 30.81   | time_units.time_unit_parse [cheetah/py2.7-numpy1.8]                   |
| +        | 125μs                | 3.81ms              | 30.42   | time_units.time_simple_unit_parse [cheetah/py2.7-numpy1.8]            |
| +        | 1.00±0s              | 3.00±0s             | 3.00?   | time_ci_small [cheetah/py2.7-numpy1.8]                                |
| +        | 1.00s                | 3.00s               | 3.00    | time_with_version_match [cheetah/py2.7-numpy1.8]                      |
| +        | 1.00s                | 3.00s               | 3.00    | time_with_version_mismatch_bench [cheetah/py2.7-numpy1.8]             |
| +        | 934μs                | 108ms               | 115.90  | time_quantity.time_quantity_init_array [cheetah/py2.7-numpy1.8]       |
| -        | 69.1μs               | 18.3μs              | 0.27    | time_units.time_unit_to [cheetah/py2.7-numpy1.8]                      |

?: 1 result(s) had too few recorded samples for the significance test (needs e.g. 7 per side); run with --record-samples and more rounds/repeat, not --quick.
"""

REFERENCE_ONLY_CHANGED_NOSTATS = """
| Change   | Before [22b920c6]    | After [fcf8c079]    | Ratio   | Benchmark (Parameter)                        |
|----------|----------------------|---------------------|---------|----------------------------------------------|
| !        | n/a                  | failed              | n/a     | params_examples.ParamSuite.track_value       |
| !        | 454μs                | failed              | n/a     | time_coordinates.time_latitude               |
| !        | 3.00s                | failed              | n/a     | time_other.time_parameterized(3)             |
| +        | 1.75ms               | 153ms               | 87.28   | time_quantity.time_quantity_array_conversion |
| +        | 1.31ms               | 7.75ms              | 5.91    | time_quantity.time_quantity_ufunc_sin        |
| +        | 372μs                | 11.5ms              | 30.81   | time_units.time_unit_parse                   |
| +        | 125μs                | 3.81ms              | 30.42   | time_units.time_simple_unit_parse            |
| +        | 1.00±1s              | 3.00±1s             | 3.00    | time_ci_big                                  |
| +        | 1.00±0s              | 3.00±0s             | 3.00    | time_ci_small                                |
| +        | 1.00s                | 3.00s               | 3.00    | time_with_version_match                      |
| +        | 1.00s                | 3.00s               | 3.00    | time_with_version_mismatch_bench             |
| +        | 934μs                | 108ms               | 115.90  | time_quantity.time_quantity_init_array       |
| -        | 69.1μs               | 18.3μs              | 0.27    | time_units.time_unit_to                      |
"""

REFERENCE_FIXED_FAIL = """
All benchmarks:

| Change   | Before [22b920c6]    | After [fcf8c079]    | Ratio   | Benchmark (Parameter)                         |
|----------|----------------------|---------------------|---------|-----------------------------------------------|
| !        | n/a                  | failed              | n/a     | params_examples.ParamSuite.track_value        |
| *        | failed               | 1.00s               | n/a     | time_AAA_failure                              |
|          | n/a                  | n/a                 | n/a     | time_AAA_skip                                 |
|          | 1.00±1s              | 3.00±1s             | ~3.00?  | time_ci_big                                   |
| +        | 1.00±0s              | 3.00±0s             | 3.00?   | time_ci_small                                 |
| !        | 454μs                | failed              | n/a     | time_coordinates.time_latitude                |
|          | 1.00s                | 1.00s               | 1.00    | time_other.time_parameterized(1)              |
|          | 2.00s                | 4.00s               | 2.00    | time_other.time_parameterized(2)              |
| !        | 3.00s                | failed              | n/a     | time_other.time_parameterized(3)              |
| +        | 1.75ms               | 153ms               | 87.28   | time_quantity.time_quantity_array_conversion  |
| +        | 934μs                | 108ms               | 115.90  | time_quantity.time_quantity_init_array        |
|          | 83.6μs               | 55.4μs              | 0.66    | time_quantity.time_quantity_init_scalar       |
|          | 282μs                | 147μs               | 0.52    | time_quantity.time_quantity_scalar_conversion |
| +        | 1.31ms               | 7.75ms              | 5.91    | time_quantity.time_quantity_ufunc_sin         |
|          | 42                   | 42                  | 1.00    | time_secondary.track_value                    |
|          | 5.73m                | 5.73m               | 1.00    | time_units.mem_unit                           |
| +        | 125μs                | 3.81ms              | 30.42   | time_units.time_simple_unit_parse             |
|          | 1.64ms               | 1.53ms              | 0.93    | time_units.time_unit_compose                  |
| +        | 372μs                | 11.5ms              | 30.81   | time_units.time_unit_parse                    |
| -        | 69.1μs               | 18.3μs              | 0.27    | time_units.time_unit_to                       |
|          | 11.9μs               | 13.1μs              | 1.10    | time_units.time_very_simple_unit_parse        |
| +        | 1.00s                | 3.00s               | 3.00    | time_with_version_match                       |
| +        | 1.00s                | 3.00s               | 3.00    | time_with_version_mismatch_bench              |
| x        | 1.00s                | 3.00s               | 3.00    | time_with_version_mismatch_other              |

?: 2 result(s) had too few recorded samples for the significance test (needs e.g. 7 per side); run with --record-samples and more rounds/repeat, not --quick.
"""


def test_compare(capsys, tmpdir, example_results):
    tmpdir = str(tmpdir)
    os.chdir(tmpdir)

    conf = config.Config.from_json(
        {
            'results_dir': example_results,
            'repo': tools.generate_test_repo(tmpdir).path,
            'project': 'asv',
            'environment_type': "shouldn't matter what",
        }
    )

    tools.run_asv_with_conf(
        conf,
        'compare',
        '22b920c6',
        'fcf8c079',
        '--machine=cheetah',
        '--factor=2',
        '--environment=py2.7-numpy1.8',
    )

    text, err = capsys.readouterr()
    assert text.strip() == REFERENCE.strip()

    tools.run_asv_with_conf(
        conf,
        'compare',
        '22b920c6',
        'fcf8c079',
        '--machine=cheetah',
        '--factor=2',
        '--split',
        '--environment=py2.7-numpy1.8',
    )
    text, err = capsys.readouterr()
    assert text.strip() == REFERENCE_SPLIT.strip()

    # Check print_table output as called from Continuous
    status = Compare.print_table(
        conf,
        '22b920c6',
        'fcf8c079',
        factor=2,
        machine='cheetah',
        split=False,
        only_changed=True,
        sort='ratio',
        env_names=["py2.7-numpy1.8"],
        commit_names={'22b920c6': 'name1', 'fcf8c079': 'name2'},
    )
    worsened, improved = status
    assert worsened
    assert improved
    text, err = capsys.readouterr()
    # Removing tailing spaces, so they don't need to be in `REFERENCE_ONLY_CHANGED`
    text = re.sub(r' *\n', r'\n', text)
    assert text.strip() == REFERENCE_ONLY_CHANGED.strip()

    # Check table with multiple environments
    status = Compare.print_table(
        conf,
        '22b920c6',
        'fcf8c079',
        factor=2,
        machine='cheetah',
        split=False,
        only_changed=True,
        sort='ratio',
    )
    text, err = capsys.readouterr()
    assert text.strip() == REFERENCE_ONLY_CHANGED_MULTIENV.strip()

    # Check results with no stats
    tools.run_asv_with_conf(
        conf,
        'compare',
        '22b920c6',
        'fcf8c079',
        '--machine=cheetah',
        '--factor=2',
        '--sort=ratio',
        '--environment=py2.7-numpy1.8',
        '--no-stats',
        '--only-changed',
    )
    text, err = capsys.readouterr()
    assert text.strip() == REFERENCE_ONLY_CHANGED_NOSTATS.strip()
    assert "time_ci_big" in text.strip()


@pytest.mark.parametrize(
    "dvcs_type",
    [
        "git",
        pytest.param("hg", marks=pytest.mark.skipif(hglib is None or WIN, reason="needs hglib")),
    ],
)
def test_compare_name_lookup(dvcs_type, capsys, tmpdir, example_results):
    tmpdir = str(tmpdir)
    os.chdir(tmpdir)

    repo = tools.generate_test_repo(tmpdir, dvcs_type=dvcs_type)

    branch_name = util.git_default_branch() if dvcs_type == 'git' else 'default'
    commit_hash = repo.get_branch_hashes(branch_name)[0]

    result_dir = os.path.join(tmpdir, 'results')

    src = os.path.join(example_results, 'cheetah')
    dst = os.path.join(result_dir, 'cheetah')
    os.makedirs(dst)

    for fn in ['feea15ca-py2.7-Cython-numpy1.8.json', 'machine.json']:
        shutil.copyfile(os.path.join(src, fn), os.path.join(dst, fn))

    shutil.copyfile(
        os.path.join(example_results, 'benchmarks.json'),
        os.path.join(result_dir, 'benchmarks.json'),
    )

    # Copy to different commit
    fn_1 = os.path.join(dst, 'feea15ca-py2.7-Cython-numpy1.8.json')
    fn_2 = os.path.join(dst, commit_hash[:8] + '-py2.7-Cython-numpy1.8.json')
    data = util.load_json(fn_1)
    data['commit_hash'] = commit_hash
    util.write_json(fn_2, data)

    conf = config.Config.from_json(
        {
            'results_dir': result_dir,
            'repo': repo.path,
            'project': 'asv',
            'environment_type': "shouldn't matter what",
        }
    )

    # Lookup with symbolic name
    tools.run_asv_with_conf(
        conf,
        'compare',
        branch_name,
        'feea15ca',
        '--machine=cheetah',
        '--factor=2',
        '--environment=py2.7-Cython-numpy1.8',
        '--only-changed',
    )

    # Nothing should be printed since no results were changed
    text, err = capsys.readouterr()
    assert text.strip() == ''


def test_compare_fixed_failures(capsys, tmpdir, example_results):
    tmpdir = str(tmpdir)
    os.chdir(tmpdir)
    before_short_hash = '22b920c6'
    after_short_hash = 'fcf8c079'
    env_spec = 'py2.7-Cython-numpy1.8'
    before_filename = f"{before_short_hash}-{env_spec}.json"
    after_filename = f"{after_short_hash}-{env_spec}.json"
    results_dir_fixed = join(tmpdir, "results_fixed")
    machine_dir = join(results_dir_fixed, 'cheetah')
    os.makedirs(machine_dir)
    # Don't need modification
    shutil.copyfile(
        join(example_results, 'benchmarks.json'), join(results_dir_fixed, 'benchmarks.json')
    )
    shutil.copyfile(
        join(example_results, 'cheetah', 'machine.json'), join(machine_dir, 'machine.json')
    )
    shutil.copyfile(
        join(example_results, 'cheetah', before_filename), join(machine_dir, before_filename)
    )
    # Load the 'after' result file, modify it, and save to the new location
    after_path = join(example_results, 'cheetah', after_filename)
    data = util.load_json(after_path)
    # Change the result of time_AAA_failure from failed (null) to a successful run with value 1.0
    data['results']['time_AAA_failure'] = [[1.0], []]
    new_after_path = join(machine_dir, after_filename)
    util.write_json(new_after_path, data)
    conf = config.Config.from_json(
        {
            'results_dir': results_dir_fixed,
            'repo': tools.generate_test_repo(tmpdir).path,
            'project': 'asv',
            'environment_type': "shouldn't matter what",
        }
    )
    tools.run_asv_with_conf(
        conf,
        'compare',
        before_short_hash,
        after_short_hash,
        '--machine=cheetah',
        '--factor=2',
        '--environment=py2.7-numpy1.8',
    )
    text, err = capsys.readouterr()
    assert text.strip() == REFERENCE_FIXED_FAIL.strip()


def test_compare_split_only_changed_has_titles(capsys, tmpdir, example_results):
    """Issue #885: --split must print group titles even with --only-changed."""
    tmpdir = str(tmpdir)
    os.chdir(tmpdir)
    conf = config.Config.from_json(
        {'results_dir': example_results,
         'repo': tools.generate_test_repo(tmpdir).path,
         'project': 'asv',
         'environment_type': "shouldn't matter what"})
    Compare.print_table(conf, '22b920c6', 'fcf8c079', factor=2, machine='cheetah',
                        split=True, only_changed=True, sort='ratio',
                        env_names=["py2.7-numpy1.8"])
    text, err = capsys.readouterr()
    assert "Benchmarks that have improved:" in text
    assert "Benchmarks that have got worse:" in text
    assert "time_units.time_unit_to" in text or "time_ci_small" in text


def test_factor_for():
    from asv.commands.compare import _factor_for

    conf = config.Config()
    assert _factor_for(conf, "time_units.time_unit_parse", 2) == 2

    conf.compare_factors = {r"^time_units\.": 40, "unit_parse": 31, "^mem_": 1.05}
    assert _factor_for(conf, "time_units.time_unit_parse", 2) == 40
    assert _factor_for(conf, "time_quantity.time_quantity_unit_parse", 2) == 31
    assert _factor_for(conf, "mem_unit", 2) == 1.05
    assert _factor_for(conf, "time_ci_small", 2) == 2


def test_compare_compare_factors(capsys, tmpdir, example_results):
    tmpdir = str(tmpdir)
    os.chdir(tmpdir)

    conf = config.Config.from_json(
        {
            'results_dir': example_results,
            'repo': tools.generate_test_repo(tmpdir).path,
            'project': 'asv',
            'environment_type': "shouldn't matter what",
            'compare_factors': {
                r'^time_units\.': 40,
                r'^time_other\.time_parameterized': 1.5,
            },
        }
    )

    tools.run_asv_with_conf(
        conf,
        'compare',
        '22b920c6',
        'fcf8c079',
        '--machine=cheetah',
        '--factor=2',
        '--environment=py2.7-numpy1.8',
    )
    text, err = capsys.readouterr()
    rows = {
        line.split('|')[-2].strip(): line.split('|')[1].strip()
        for line in text.splitlines()
        if line.startswith('|') and not line.startswith('|--')
    }

    assert rows['time_units.time_unit_parse'] == ''
    assert rows['time_units.time_simple_unit_parse'] == ''
    assert rows['time_units.time_unit_to'] == ''
    assert rows['time_other.time_parameterized(1)'] == ''
    assert rows['time_other.time_parameterized(2)'] == '+'
    assert rows['time_quantity.time_quantity_init_array'] == '+'
    assert rows['time_ci_small'] == '+'


@pytest.mark.parametrize(
    "n_a, n_b, expected",
    [
        (0, 0, False),
        (0, 50, False),
        (1, 1, False),
        (5, 5, False),
        (6, 6, False),
        (5, 7, False),
        (4, 9, False),
        (2, 31, False),
        (1, 500, False),
        (6, 7, True),
        (7, 7, True),
        (5, 8, True),
        (4, 10, True),
        (30, 30, True),
    ],
)
def test_mann_whitney_u_can_reject(n_a, n_b, expected):
    assert _stats.mann_whitney_u_can_reject(n_a, n_b) == expected
    assert _stats.mann_whitney_u_can_reject(n_b, n_a) == expected

    a = [1.0 + 1e-3 * j for j in range(n_a)]
    b = [3.0 + 1e-3 * j for j in range(n_b)]
    stats_a = {'ci_99_a': 1.0, 'ci_99_b': 1.0}
    stats_b = {'ci_99_a': 1.0, 'ci_99_b': 1.0}
    if expected:
        assert _stats.is_different(a, b, stats_a, stats_b)


def _sampled_results(rows):
    for name, value, samples, repeat in rows:
        values = [x for x in samples or [value] if x == x]
        stats = None
        if repeat is not None:
            stats = {
                'ci_99_a': min(values) * 0.99,
                'ci_99_b': max(values) * 1.01,
                'q_25': min(values),
                'q_75': max(values),
                'repeat': repeat,
                'number': 1,
            }
        yield name, [], [value], [stats], [samples], None, 'cheetah', 'py'


def _few_samples_table(capsys, tmpdir, example_results, rows_1, rows_2, **kwargs):
    os.chdir(str(tmpdir))
    conf = config.Config.from_json(
        {
            'results_dir': example_results,
            'repo': tools.generate_test_repo(str(tmpdir)).path,
            'project': 'asv',
            'environment_type': "shouldn't matter what",
        }
    )
    Compare.print_table(
        conf,
        '22b920c6',
        'fcf8c079',
        factor=1.1,
        split=False,
        resultset_1=_sampled_results(rows_1),
        resultset_2=_sampled_results(rows_2),
        **kwargs,
    )
    text, err = capsys.readouterr()
    rows = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.split('|')]
        if len(cells) == 7 and cells[5].startswith('time_'):
            rows[cells[5]] = (cells[1], cells[4])
    return text, rows


def _samples(value, n):
    return [value * (1 + 0.01 * (j % 3 - 1)) for j in range(n)]


NAN = float('nan')

FEW_SAMPLES_BEFORE = [
    ('time_ci_small', 1.0, _samples(1.0, 7), 7),
    ('time_units.mem_unit', 1.0, _samples(1.0, 6), 6),
    ('time_ci_big', 1.0, _samples(1.0, 5), 5),
    ('time_quantity.time_quantity_init_array', 1.0, _samples(1.0, 6), 6),
    ('time_units.time_simple_unit_parse', 1.0, _samples(1.0, 5) + [NAN, NAN], 7),
    ('time_units.time_unit_to', 1.0, None, 10),
    ('time_units.time_unit_parse', 1.0, [1.0], 1),
    ('time_units.time_unit_compose', 1.0, _samples(1.0, 3), 3),
    ('time_secondary.track_value', 1.0, None, None),
]

FEW_SAMPLES_AFTER = [
    ('time_ci_small', 3.0, _samples(3.0, 7), 7),
    ('time_units.mem_unit', 3.0, _samples(3.0, 7), 7),
    ('time_ci_big', 3.0, _samples(3.0, 5), 5),
    ('time_quantity.time_quantity_init_array', 3.0, _samples(3.0, 6), 6),
    ('time_units.time_simple_unit_parse', 3.0, _samples(3.0, 7), 7),
    ('time_units.time_unit_to', 0.3, None, 10),
    ('time_units.time_unit_parse', 3.0, [3.0], 1),
    ('time_units.time_unit_compose', 1.01, _samples(1.01, 3), 3),
    ('time_secondary.track_value', 3.0, None, None),
]

ENOUGH_SAMPLES = ('time_ci_small', 'time_units.mem_unit')


def test_compare_marks_few_samples(capsys, tmpdir, example_results):
    text, rows = _few_samples_table(
        capsys, tmpdir, example_results, FEW_SAMPLES_BEFORE, FEW_SAMPLES_AFTER
    )
    assert rows == {
        # 7 vs 7 and 6 vs 7 samples: the Mann-Whitney U test can decide
        'time_ci_small': ('+', '3.00'),
        'time_units.mem_unit': ('+', '3.00'),
        # 5 vs 5: falls back to the confidence intervals
        'time_ci_big': ('+', '3.00?'),
        # 6 vs 6 and (NaNs dropped) 5 vs 7: the test runs but cannot reject
        'time_quantity.time_quantity_init_array': ('', '~3.00?'),
        'time_units.time_simple_unit_parse': ('', '~3.00?'),
        # no recorded samples, and a single sample as with --quick
        'time_units.time_unit_to': ('-', '0.30?'),
        'time_units.time_unit_parse': ('+', '3.00?'),
        # within the factor, so the sample count does not matter
        'time_units.time_unit_compose': ('', '1.01'),
        # no statistics at all (e.g. track_ benchmarks): nothing to record
        'time_secondary.track_value': ('+', '3.00'),
    }
    footer = [line for line in text.splitlines() if line.startswith('?:')]
    assert footer == [
        "?: 5 result(s) had too few recorded samples for the significance test "
        "(needs e.g. 7 per side); run with --record-samples and more rounds/repeat, "
        "not --quick."
    ]

    text, rows = _few_samples_table(
        capsys, tmpdir, example_results, FEW_SAMPLES_BEFORE, FEW_SAMPLES_AFTER, only_changed=True
    )
    assert set(rows) == {
        'time_ci_small',
        'time_units.mem_unit',
        'time_ci_big',
        'time_units.time_unit_to',
        'time_units.time_unit_parse',
        'time_secondary.track_value',
    }
    assert "?: 3 result(s) had too few recorded samples" in text


def test_compare_few_samples_footer_only_when_needed(capsys, tmpdir, example_results):
    before = [row for row in FEW_SAMPLES_BEFORE if row[0] in ENOUGH_SAMPLES]
    after = [row for row in FEW_SAMPLES_AFTER if row[0] in ENOUGH_SAMPLES]
    text, rows = _few_samples_table(capsys, tmpdir, example_results, before, after)
    # tabulate reformats an all-numeric ratio column
    assert rows == {'time_ci_small': ('+', '3'), 'time_units.mem_unit': ('+', '3')}
    assert '?' not in text

    for kwargs in ({'mark_few_samples': False}, {'use_stats': False}):
        text, rows = _few_samples_table(
            capsys, tmpdir, example_results, FEW_SAMPLES_BEFORE, FEW_SAMPLES_AFTER, **kwargs
        )
        assert rows['time_ci_big'][0] == '+'
        assert float(rows['time_ci_big'][1]) == 3
        assert rows['time_units.time_unit_to'][0] == '-'
        assert float(rows['time_units.time_unit_to'][1]) == 0.3
        assert '?' not in text


def test_compare_no_mark_few_samples_option(capsys, tmpdir, example_results):
    os.chdir(str(tmpdir))
    conf = config.Config.from_json(
        {
            'results_dir': example_results,
            'repo': tools.generate_test_repo(str(tmpdir)).path,
            'project': 'asv',
            'environment_type': "shouldn't matter what",
        }
    )
    tools.run_asv_with_conf(
        conf,
        'compare',
        '22b920c6',
        'fcf8c079',
        '--machine=cheetah',
        '--factor=2',
        '--environment=py2.7-numpy1.8',
        '--no-mark-few-samples',
    )
    text, err = capsys.readouterr()
    assert '?' not in text
    assert '| ~3.00   | time_ci_big' in text
    assert '| 3.00    | time_ci_small' in text
