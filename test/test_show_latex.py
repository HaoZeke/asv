# Licensed under a 3-clause BSD style license - see LICENSE.rst
import os

import pytest

from asv import config, util
from asv.commands.show import Show
from asv.results import Results

from . import tools

REFERENCE_SHOW_LATEX = r"""
% Requires \usepackage{booktabs,siunitx}

\noindent
\textbf{Commit fcf8c079:}

\begin{tabular}{llr}
\toprule
Benchmark & Parameters & Value \\
\midrule
params\_examples.ParamSuite.track\_value & 'a' & --- \\
params\_examples.ParamSuite.track\_value & 'b' & --- \\
params\_examples.ParamSuite.track\_value & 'c' & --- \\
time\_AAA\_failure &  & --- \\
time\_AAA\_skip &  & --- \\
time\_ci\_big &  & \qty{3.00 \pm 1}{\second} \\
time\_ci\_small &  & \qty{3.00 \pm 0}{\second} \\
time\_coordinates.time\_latitude &  & --- \\
time\_other.time\_parameterized & 1 & \qty{1.00}{\second} \\
time\_other.time\_parameterized & 2 & \qty{4.00}{\second} \\
time\_other.time\_parameterized & 3 & --- \\
time\_quantity.time\_quantity\_array\_conversion &  & \qty{153}{\milli\second} \\
time\_quantity.time\_quantity\_init\_array &  & \qty{108}{\milli\second} \\
time\_quantity.time\_quantity\_init\_scalar &  & \qty{55.4}{\micro\second} \\
time\_quantity.time\_quantity\_scalar\_conversion &  & \qty{147}{\micro\second} \\
time\_quantity.time\_quantity\_ufunc\_sin &  & \qty{7.75}{\milli\second} \\
time\_secondary.track\_value &  & \num{42} \\
time\_units.mem\_unit &  & \qty{5.73}{\minute} \\
time\_units.time\_simple\_unit\_parse &  & \qty{3.81}{\milli\second} \\
time\_units.time\_unit\_compose &  & \qty{1.53}{\milli\second} \\
time\_units.time\_unit\_parse &  & \qty{11.5}{\milli\second} \\
time\_units.time\_unit\_to &  & \qty{18.3}{\micro\second} \\
time\_units.time\_very\_simple\_unit\_parse &  & \qty{13.1}{\micro\second} \\
time\_with\_version\_match &  & \qty{3.00}{\second} \\
\bottomrule
\end{tabular}
"""

REFERENCE_SHOW_ESCAPE = r"""
% Requires \usepackage{booktabs,siunitx}

\noindent
\textbf{Commit showaaa:}

\begin{tabular}{llr}
\toprule
Benchmark & Parameters & Value \\
\midrule
both\_failed &  & --- \\
bytes\_ok &  & \qty{2}{\kilo\byte} \\
cost\_\&\%\#\$\{\}\textasciitilde{}\textasciicircum{}\textbackslash{} &  & \qty{1.50}{\second} \\
group.time\_param & \_\&\%\#\$\{\}\textasciitilde{}\textasciicircum{}\textbackslash{} line, ok & \qty{1.00}{\second} \\
group.time\_param & \_\&\%\#\$\{\}\textasciitilde{}\textasciicircum{}\textbackslash{} line, skip & --- \\
group.time\_param & plain, ok & --- \\
group.time\_param & plain, skip & \qty{2.00}{\milli\second} \\
\bottomrule
\end{tabular}
"""


def _conf(tmpdir, results_dir):
    os.chdir(str(tmpdir))
    return config.Config.from_json(
        {
            'results_dir': results_dir,
            'repo': tools.generate_test_repo(str(tmpdir)).path,
            'project': 'asv',
            'environment_type': "shouldn't matter what",
        }
    )


def test_show_latex_golden(capsys, tmpdir, example_results):
    conf = _conf(tmpdir, example_results)
    tools.run_asv_with_conf(
        conf,
        'show',
        'fcf8c079',
        '--machine=cheetah',
        '--environment=py2.7-numpy1.8',
        '--format=latex',
    )
    text, err = capsys.readouterr()
    assert text.strip() == REFERENCE_SHOW_LATEX.strip()

    tools.run_asv_with_conf(
        conf,
        'show',
        'fcf8c079',
        '--machine=cheetah',
        '--environment=py2.7-numpy1.8',
        '--format=latex',
        '--only-body',
    )
    text, err = capsys.readouterr()
    assert r'\begin{tabular}' not in text
    assert r'\toprule' not in text
    assert 'Benchmark' not in text
    assert r'time\_other.time\_parameterized & 1 & \qty{1.00}{\second} \\' in text
    assert r'time\_other.time\_parameterized & 3 & --- \\' in text
    assert r'time\_AAA\_failure &  & --- \\' in text
    assert r'time\_AAA\_skip &  & --- \\' in text
    assert text.strip().endswith(r'\\')


def test_show_latex_labels_several_environments(capsys, tmpdir, example_results):
    conf = _conf(tmpdir, example_results)
    tools.run_asv_with_conf(
        conf,
        'show',
        'fcf8c079',
        '--machine=cheetah',
        '--bench=time_quantity.time_quantity_array_conversion',
        '--format=latex',
    )
    text, err = capsys.readouterr()
    assert r'time\_quantity.time\_quantity\_array\_conversion [cheetah/py2.7-numpy1.8]' in text
    assert r'time\_quantity.time\_quantity\_array\_conversion [cheetah/py2.7-foo-numpy1.8]' in text
    assert text.count(r'\begin{tabular}') == 1


def test_show_latex_requires_commit_and_body_flag():
    with pytest.raises(util.UserError, match='requires a commit'):
        Show.run(None, table_format='latex')
    with pytest.raises(util.UserError, match='only-body'):
        Show.run(None, commit='abc', only_body=True)
    with pytest.raises(util.UserError, match='durations'):
        Show.run(None, commit='abc', durations=True, table_format='latex')


def _put_result(res, name, values, params):
    if not isinstance(values, list):
        values = [values]
    if all(value is None for value in values):
        stored = None
    else:
        stored = list(values)
    res._results[name] = stored
    res._stats[name] = None
    res._samples[name] = None
    res._benchmark_params[name] = params
    res._benchmark_version[name] = '1'


def test_show_latex_escaping_params_failed(capsys, tmpdir):
    results_dir = os.path.join(str(tmpdir), 'results')
    machine_dir = os.path.join(results_dir, 'cheetah')
    os.makedirs(machine_dir)
    util.write_json(
        os.path.join(machine_dir, 'machine.json'),
        {
            'machine': 'cheetah',
            'arch': 'x86_64',
            'cpu': 'cpu',
            'os': 'Linux',
            'ram': '1G',
        },
        api_version=1,
    )

    special = 'cost_&%#${}~^\\'
    param = '_&%#${}~^\\\nline'
    benches = {
        special: {
            'name': special,
            'params': [],
            'param_names': [],
            'unit': 'seconds',
            'version': '1',
        },
        'group.time_param': {
            'name': 'group.time_param',
            'params': [[param, 'plain'], ['ok', 'skip']],
            'param_names': ['p', 'q'],
            'unit': 'seconds',
            'version': '1',
        },
        'both_failed': {
            'name': 'both_failed',
            'params': [],
            'param_names': [],
            'unit': 'seconds',
            'version': '1',
        },
        'bytes_ok': {
            'name': 'bytes_ok',
            'params': [],
            'param_names': [],
            'unit': 'bytes',
            'version': '1',
        },
    }
    util.write_json(os.path.join(results_dir, 'benchmarks.json'), benches, api_version=2)

    res = Results(
        {
            'machine': 'cheetah',
            'arch': 'x86_64',
            'cpu': 'cpu',
            'os': 'Linux',
            'ram': '1G',
        },
        {},
        'showaaa',
        1,
        '3.12',
        'py3',
        {},
    )
    _put_result(res, special, 1.5, [])
    _put_result(
        res,
        'group.time_param',
        [1.0, None, float('nan'), 0.002],
        [[param, 'plain'], ['ok', 'skip']],
    )
    _put_result(res, 'both_failed', None, [])
    _put_result(res, 'bytes_ok', 2000, [])
    res.save(results_dir)

    conf = _conf(tmpdir, results_dir)
    tools.run_asv_with_conf(
        conf,
        'show',
        'showaaa',
        '--machine=cheetah',
        '--format=latex',
    )
    text, err = capsys.readouterr()
    assert text.strip() == REFERENCE_SHOW_ESCAPE.strip()
    param_rows = [line for line in text.splitlines() if 'line,' in line]
    assert len(param_rows) == 2
    assert all(line.endswith(r'\\') for line in param_rows)
