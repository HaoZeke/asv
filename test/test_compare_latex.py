# Licensed under a 3-clause BSD style license - see LICENSE.rst
import math
import os

import pytest

from asv import config, runner, util
from asv.commands.compare import (
    Compare,
    format_latex_interval,
    format_latex_ratio,
    format_latex_tabular,
    format_latex_value,
    latex_escape,
)
from asv.results import Results

from . import tools

REFERENCE_LATEX = r"""
% Requires \usepackage{booktabs,siunitx}

\noindent
\textbf{All benchmarks:}

\begin{tabular}{rrrl}
\toprule
Before [\texttt{22b920c6}] & After [\texttt{fcf8c079}] & Ratio & Benchmark \\
\midrule
n/a & failed & ! n/a & params\_examples.ParamSuite.track\_value \\
failed & failed & n/a & time\_AAA\_failure \\
n/a & n/a & n/a & time\_AAA\_skip \\
\qty{1.00 \pm 1}{\second} & \qty{3.00 \pm 1}{\second} & \textasciitilde{}\num{3.00} & time\_ci\_big \\
\qty{1.00 \pm 0}{\second} & \qty{3.00 \pm 0}{\second} & + \num{3.00} & time\_ci\_small \\
\qty{454}{\micro\second} & failed & ! n/a & time\_coordinates.time\_latitude \\
\qty{1.00}{\second} & \qty{1.00}{\second} & \num{1.00} & time\_other.time\_parameterized(1) \\
\qty{2.00}{\second} & \qty{4.00}{\second} & \num{2.00} & time\_other.time\_parameterized(2) \\
\qty{3.00}{\second} & failed & ! n/a & time\_other.time\_parameterized(3) \\
\qty{1.75}{\milli\second} & \qty{153}{\milli\second} & + \num{87.28} & time\_quantity.time\_quantity\_array\_conversion \\
\qty{934}{\micro\second} & \qty{108}{\milli\second} & + \num{115.90} & time\_quantity.time\_quantity\_init\_array \\
\qty{83.6}{\micro\second} & \qty{55.4}{\micro\second} & \num{0.66} & time\_quantity.time\_quantity\_init\_scalar \\
\qty{282}{\micro\second} & \qty{147}{\micro\second} & \num{0.52} & time\_quantity.time\_quantity\_scalar\_conversion \\
\qty{1.31}{\milli\second} & \qty{7.75}{\milli\second} & + \num{5.91} & time\_quantity.time\_quantity\_ufunc\_sin \\
\num{42} & \num{42} & \num{1.00} & time\_secondary.track\_value \\
\qty{5.73}{\minute} & \qty{5.73}{\minute} & \num{1.00} & time\_units.mem\_unit \\
\qty{125}{\micro\second} & \qty{3.81}{\milli\second} & + \num{30.42} & time\_units.time\_simple\_unit\_parse \\
\qty{1.64}{\milli\second} & \qty{1.53}{\milli\second} & \num{0.93} & time\_units.time\_unit\_compose \\
\qty{372}{\micro\second} & \qty{11.5}{\milli\second} & + \num{30.81} & time\_units.time\_unit\_parse \\
\qty{69.1}{\micro\second} & \qty{18.3}{\micro\second} & - \num{0.27} & time\_units.time\_unit\_to \\
\qty{11.9}{\micro\second} & \qty{13.1}{\micro\second} & \num{1.10} & time\_units.time\_very\_simple\_unit\_parse \\
\qty{1.00}{\second} & \qty{3.00}{\second} & + \num{3.00} & time\_with\_version\_match \\
\qty{1.00}{\second} & \qty{3.00}{\second} & + \num{3.00} & time\_with\_version\_mismatch\_bench \\
\qty{1.00}{\second} & \qty{3.00}{\second} & x \num{3.00} & time\_with\_version\_mismatch\_other \\
\bottomrule
\end{tabular}
"""

REFERENCE_LATEX_ESCAPE = r"""
% Requires \usepackage{booktabs,siunitx}

\noindent
\textbf{All benchmarks:}

\begin{tabular}{rrrl}
\toprule
Before [\texttt{latexaaa}] & After [\texttt{latexbbb}] & Ratio & Benchmark \\
\midrule
failed & failed & n/a & both\_failed \\
\qty{1.00}{\second} & \qty{3.00}{\second} & + \num{3.00} & cost\_\&\%\#\$\{\}\textasciitilde{}\textasciicircum{}\textbackslash{} \\
\qty{1.00}{\second} & \qty{4.00}{\second} & + \num{4.00} & group.time\_param(\_\&\%\#\$\{\}\textasciitilde{}\textasciicircum{}\textbackslash{} line) \\
\qty{1.00}{\second} & failed & ! n/a & group.time\_param(plain) \\
n/a & n/a & n/a & group.time\_param(skip) \\
\qty{1.00}{\second} & failed & ! n/a & introduced \\
n/a & \qty{2}{\kilo\byte} & n/a & only\_after \\
\bottomrule
\end{tabular}
"""


def _compare_conf(tmpdir, example_results):
    tmpdir = str(tmpdir)
    os.chdir(tmpdir)
    return config.Config.from_json(
        {
            'results_dir': example_results,
            'repo': tools.generate_test_repo(tmpdir).path,
            'project': 'asv',
            'environment_type': "shouldn't matter what",
        }
    )


def test_latex_escape_specials():
    raw = '_&%#${}~^\\'
    assert latex_escape(raw) == (
        r'\_\&\%\#\$\{\}\textasciitilde{}\textasciicircum{}\textbackslash{}'
    )
    assert latex_escape('\\') == r'\textbackslash{}'
    assert latex_escape('_') == r'\_'
    assert latex_escape('&') == r'\&'
    assert latex_escape('%') == r'\%'
    assert latex_escape('#') == r'\#'
    assert latex_escape('$') == r'\$'
    assert latex_escape('{') == r'\{'
    assert latex_escape('}') == r'\}'
    assert latex_escape('~') == r'\textasciitilde{}'
    assert latex_escape('^') == r'\textasciicircum{}'
    assert latex_escape('time_other.ok') == r'time\_other.ok'
    # A backslash must be replaced before the braces it expands to.
    assert r'\{' not in latex_escape('\\')


def test_format_latex_value_and_ratio():
    assert format_latex_value(1e-9, 'seconds') == r'\qty{1.00}{\nano\second}'
    assert format_latex_value(1.1e-6, 'seconds') == r'\qty{1.10}{\micro\second}'
    assert format_latex_value(1.12e-3, 'seconds') == r'\qty{1.12}{\milli\second}'
    assert format_latex_value(1.123, 'seconds') == r'\qty{1.12}{\second}'
    assert format_latex_value(60, 'seconds') == r'\qty{1.00}{\minute}'
    assert format_latex_value(2 * 3600, 'seconds') == r'\qty{2.00}{\hour}'
    assert format_latex_value(6 * 86400, 'seconds') == r'\qty{6.00}{\day}'
    assert format_latex_value(7 * 86400, 'seconds') == r'\num{1.00}\,\text{w}'
    assert format_latex_value(1, 'seconds', err=1) == r'\qty{1.00 \pm 1}{\second}'
    assert format_latex_value(344, 'seconds') == r'\qty{5.73}{\minute}'
    assert format_latex_value(0, 'seconds') == r'\qty{0}{\second}'

    assert format_latex_value(1, 'bytes') == r'\qty{1}{\byte}'
    assert format_latex_value(999, 'bytes') == r'\qty{999}{\byte}'
    assert format_latex_value(1000, 'bytes') == r'\qty{1}{\kilo\byte}'
    assert format_latex_value(1.1e6, 'bytes') == r'\qty{1.1}{\mega\byte}'
    assert format_latex_value(1e3, 'bytes', err=123) == r'\qty{1 \pm 0.1}{\kilo\byte}'

    assert format_latex_value(42, 'unit') == r'\num{42}'
    assert format_latex_value(1.5, 'flop') == r'\num{1.5}\,\text{flop}'
    assert format_latex_value(None, 'seconds') == 'failed'
    assert format_latex_value(float('nan'), 'seconds') == 'n/a'
    assert format_latex_value(math.nan, 'bytes') == 'n/a'

    assert format_latex_ratio('+', '  3.00') == r'+ \num{3.00}'
    assert format_latex_ratio('-', '0.27') == r'- \num{0.27}'
    assert format_latex_ratio(' ', '~3.00') == r'\textasciitilde{}\num{3.00}'
    assert format_latex_ratio('!', 'n/a') == '! n/a'
    assert format_latex_ratio('*', 'n/a') == '* n/a'
    assert format_latex_ratio('x', '3.00') == r'x \num{3.00}'
    assert format_latex_ratio(' ', '  1.00') == r'\num{1.00}'
    assert format_latex_ratio(' ', 'n/a') == 'n/a'


REFERENCE_LATEX_CI = r"""
% Requires \usepackage{booktabs,siunitx}

\noindent
\textbf{All benchmarks:}

\begin{tabular}{rrrrl}
\toprule
Before [\texttt{aaaaaaaa}] & After [\texttt{bbbbbbbb}] & Ratio CI & A/A & Benchmark \\
\midrule
\qty{1.00}{\second} & \qty{2.00}{\second} & + \num{2.00}\,[\num{2.00}, \num{2.00}] & \num{1.00}\,[\num{1.00}, \num{1.00}] & bench.time\_it \\
\bottomrule
\end{tabular}
"""


def test_format_latex_interval():
    assert format_latex_interval((1.03, 1.02, 1.04)) == (r'\num{1.03}\,[\num{1.02}, \num{1.04}]')
    assert format_latex_interval((2.0, 2.0, 2.0), '+') == (
        r'+ \num{2.00}\,[\num{2.00}, \num{2.00}]'
    )
    assert format_latex_interval(None) == 'n/a'
    assert format_latex_interval(None, '!') == '! n/a'


def test_format_latex_tabular_only_body():
    rows = [(r'\num{1}', r'\num{2}', r'\num{2.00}', r'bench\_a')]
    headers = ['Before', 'After', 'Ratio', 'Benchmark']
    body = format_latex_tabular(rows, headers, only_body=True)
    assert body == r'\num{1} & \num{2} & \num{2.00} & bench\_a \\'
    assert r'\begin{tabular}' not in body
    full = format_latex_tabular(rows, headers, only_body=False)
    assert full.startswith(r'\begin{tabular}{rrrl}')
    assert r'\toprule' in full
    assert r'\bottomrule' in full
    assert full.endswith(r'\end{tabular}')


def test_compare_latex_golden(capsys, tmpdir, example_results):
    conf = _compare_conf(tmpdir, example_results)
    tools.run_asv_with_conf(
        conf,
        'compare',
        '22b920c6',
        'fcf8c079',
        '--machine=cheetah',
        '--factor=2',
        '--environment=py2.7-numpy1.8',
        '--format=latex',
    )
    text, err = capsys.readouterr()
    assert text.strip() == REFERENCE_LATEX.strip()

    # Commit names land in the column headings, escaped for LaTeX.
    Compare.print_table(
        conf,
        '22b920c6',
        'fcf8c079',
        factor=2,
        split=False,
        machine='cheetah',
        env_names=['py2.7-numpy1.8'],
        commit_names={'22b920c6': 'name_1', 'fcf8c079': 'v0.2'},
        table_format='latex',
    )
    text, err = capsys.readouterr()
    assert r'\texttt{<name\_1>}' in text
    assert r'\texttt{<v0.2>}' in text


def test_compare_latex_only_body_and_split(capsys, tmpdir, example_results):
    conf = _compare_conf(tmpdir, example_results)
    tools.run_asv_with_conf(
        conf,
        'compare',
        '22b920c6',
        'fcf8c079',
        '--machine=cheetah',
        '--factor=2',
        '--environment=py2.7-numpy1.8',
        '--format=latex',
        '--only-body',
    )
    text, err = capsys.readouterr()
    assert r'\begin{tabular}' not in text
    assert r'\toprule' not in text
    assert r'\midrule' not in text
    assert r'\bottomrule' not in text
    assert r'\end{tabular}' not in text
    assert 'Before' not in text
    assert (
        r'\qty{1.00}{\second} & \qty{1.00}{\second} & \num{1.00} '
        r'& time\_other.time\_parameterized(1) \\'
    ) in text
    assert r'failed & failed & n/a & time\_AAA\_failure \\' in text
    assert r'n/a & n/a & n/a & time\_AAA\_skip \\' in text
    assert r'n/a & failed & ! n/a & params\_examples.ParamSuite.track\_value \\' in text
    assert r'\textasciitilde{}\num{3.00}' in text
    assert r'+ \num{3.00}' in text
    assert r'- \num{0.27}' in text
    assert text.strip().endswith(r'\\')

    tools.run_asv_with_conf(
        conf,
        'compare',
        '22b920c6',
        'fcf8c079',
        '--machine=cheetah',
        '--factor=2',
        '--environment=py2.7-numpy1.8',
        '--format=latex',
        '--split',
    )
    text, err = capsys.readouterr()
    assert text.count(r'\begin{tabular}') == 4
    assert r'\textbf{Benchmarks that have improved:}' in text
    assert r'\textbf{Benchmarks that have stayed the same:}' in text
    assert r'\textbf{Benchmarks that have got worse:}' in text
    assert r'\textbf{Benchmarks that are not comparable:}' in text
    improved, rest = text.split(r'\textbf{Benchmarks that have stayed the same:}', 1)
    assert r'- \num{0.27}' in improved
    assert r'+ \num{87.28}' not in improved
    assert r'x \num{3.00}' in text

    tools.run_asv_with_conf(
        conf,
        'compare',
        '22b920c6',
        'fcf8c079',
        '--machine=cheetah',
        '--factor=2',
        '--environment=py2.7-numpy1.8',
        '--format=latex',
        '--only-body',
        '--split',
    )
    text, err = capsys.readouterr()
    assert r'\begin{tabular}' not in text
    assert '% Benchmarks that have improved:' in text
    assert '% Benchmarks that have got worse:' in text
    assert '% Benchmarks that are not comparable:' in text


def test_only_body_requires_latex(capsys, tmpdir, example_results):
    conf = _compare_conf(tmpdir, example_results)
    with pytest.raises(util.UserError, match='only-body'):
        tools.run_asv_with_conf(
            conf,
            'compare',
            '22b920c6',
            'fcf8c079',
            '--machine=cheetah',
            '--only-body',
        )


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


def _write_escape_repo(tmpdir):
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
    # Newline in the parameter must collapse, and every LaTeX special is escaped.
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
            'params': [[param, 'plain', 'skip']],
            'param_names': ['p'],
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
        'introduced': {
            'name': 'introduced',
            'params': [],
            'param_names': [],
            'unit': 'seconds',
            'version': '1',
        },
        'only_after': {
            'name': 'only_after',
            'params': [],
            'param_names': [],
            'unit': 'bytes',
            'version': '1',
        },
    }
    util.write_json(os.path.join(results_dir, 'benchmarks.json'), benches, api_version=2)

    def make(commit):
        return Results(
            {
                'machine': 'cheetah',
                'arch': 'x86_64',
                'cpu': 'cpu',
                'os': 'Linux',
                'ram': '1G',
            },
            {},
            commit,
            1,
            '3.12',
            'py3',
            {},
        )

    before = make('latexaaa')
    _put_result(before, special, 1.0, [])
    _put_result(before, 'group.time_param', [1.0, 1.0, float('nan')], [[param, 'plain', 'skip']])
    _put_result(before, 'both_failed', None, [])
    _put_result(before, 'introduced', 1.0, [])
    before.save(results_dir)

    after = make('latexbbb')
    _put_result(after, special, 3.0, [])
    _put_result(after, 'group.time_param', [4.0, None, float('nan')], [[param, 'plain', 'skip']])
    _put_result(after, 'both_failed', None, [])
    _put_result(after, 'introduced', None, [])
    _put_result(after, 'only_after', 2000, [])
    after.save(results_dir)

    os.chdir(str(tmpdir))
    return config.Config.from_json(
        {
            'results_dir': results_dir,
            'repo': tools.generate_test_repo(str(tmpdir)).path,
            'project': 'asv',
            'environment_type': "shouldn't matter what",
        }
    )


def test_compare_latex_escaping_params_failed_missing(capsys, tmpdir):
    conf = _write_escape_repo(tmpdir)
    tools.run_asv_with_conf(
        conf,
        'compare',
        'latexaaa',
        'latexbbb',
        '--machine=cheetah',
        '--factor=2',
        '--format=latex',
    )
    text, err = capsys.readouterr()
    assert text.strip() == REFERENCE_LATEX_ESCAPE.strip()
    # The newline inside the parameter is collapsed onto that benchmark's row.
    param_rows = [line for line in text.splitlines() if 'line)' in line]
    assert param_rows == [
        r'\qty{1.00}{\second} & \qty{4.00}{\second} & + \num{4.00} '
        r'& group.time\_param(\_\&\%\#\$\{\}\textasciitilde{}'
        r'\textasciicircum{}\textbackslash{} line) \\'
    ]


def test_compare_latex_ci_and_aa_columns(capsys, tmpdir):
    results_dir = os.path.join(str(tmpdir), 'results')
    machine_dir = os.path.join(results_dir, 'cheetah')
    os.makedirs(machine_dir)
    util.write_json(
        os.path.join(machine_dir, 'machine.json'),
        {'machine': 'cheetah'},
        api_version=1,
    )
    util.write_json(
        os.path.join(results_dir, 'benchmarks.json'),
        {
            'bench.time_it': {
                'name': 'bench.time_it',
                'params': [],
                'param_names': [],
                'unit': 'seconds',
                'version': '1',
            }
        },
        api_version=2,
    )
    params = {
        'machine': 'cheetah',
        'arch': 'x86_64',
        'cpu': 'cpu',
        'os': 'Linux',
        'ram': '1G',
    }
    benchmark = {'name': 'bench.time_it', 'version': '1', 'params': []}
    before = Results(params, {}, 'a' * 40, 1, '3.12', 'py3', {})
    after = Results(params, {}, 'b' * 40, 2, '3.12', 'py3', {})
    repeated = Results(params, {}, 'a' * 40, 1, '3.12', 'py3~aa', {})
    before_row = runner.BenchmarkResult(
        result=[1.0],
        samples=[[1.0, 1.0, 1.0, 1.0]],
        number=[None],
        errcode=0,
        stderr='',
        profile=None,
    )
    after_row = runner.BenchmarkResult(
        result=[2.0],
        samples=[[2.0, 2.0, 2.0, 2.0]],
        number=[None],
        errcode=0,
        stderr='',
        profile=None,
    )
    before.add_result(benchmark, before_row, record_samples=True, round_number=1)
    after.add_result(benchmark, after_row, record_samples=True, round_number=1)
    repeated.add_result(benchmark, before_row, record_samples=True, round_number=1)
    # A later failed round stays in the file and does not replace the samples.
    failed = runner.BenchmarkResult(
        result=[None],
        samples=[None],
        number=[None],
        errcode=1,
        stderr='boom',
        profile=None,
    )
    after.add_result(benchmark, failed, record_samples=True, append_samples=True, round_number=2)
    after.set_comparison(
        'a' * 40,
        {'bench.time_it': [[2.0, 2.0, 2.0]]},
        {'bench.time_it': [[1.0, 1.0, 1.0]]},
    )
    before.save(results_dir)
    after.save(results_dir)
    repeated.save(results_dir)

    loaded = Results.load(os.path.join(results_dir, after._filename))
    assert loaded.round_samples['bench.time_it'][0] == [[2.0, 2.0, 2.0, 2.0]]
    assert loaded.round_samples['bench.time_it'][1] is None
    assert loaded.failed_rounds['bench.time_it'] == [2]
    assert loaded.get_result_samples('bench.time_it', []) == [[2.0, 2.0, 2.0, 2.0]]
    assert loaded.get_comparison('aaaaaaaa')['ratio_ci']['bench.time_it'] == [[2.0, 2.0, 2.0]]
    raw = util.load_json(os.path.join(results_dir, before._filename))
    assert 'result_columns' in raw
    assert raw['interleaved']['round_samples']['bench.time_it'] == [[[1.0, 1.0, 1.0, 1.0]]]

    os.chdir(str(tmpdir))
    conf = config.Config.from_json(
        {
            'results_dir': results_dir,
            'repo': tools.generate_test_repo(str(tmpdir)).path,
            'project': 'asv',
            'environment_type': "shouldn't matter what",
        }
    )
    worsened, improved = Compare.print_table(
        conf,
        'a' * 40,
        'b' * 40,
        factor=1.1,
        split=False,
        machine='cheetah',
        table_format='latex',
        columns='before,after,ci,aa,benchmark',
    )
    text, err = capsys.readouterr()
    table, _end, rest = text.partition(r'\end{tabular}')
    assert (table + r'\end{tabular}').strip() == REFERENCE_LATEX_CI.strip()
    assert 'round 2 failed' in text + err + rest
    assert worsened
    assert not improved
    # The repeated base is an A/A slot, not a third revision in the table.
    assert text.count(r'bench.time\_it') == 1
    assert 'py3~aa' not in text

    with pytest.raises(util.UserError, match='columns'):
        Compare.print_table(
            conf,
            'a' * 40,
            'b' * 40,
            factor=1.1,
            split=False,
            machine='cheetah',
            table_format='github',
            columns='ci',
        )


def test_compare_latex_adaptive_rounds_verdict(capsys, tmpdir):
    """An adaptive stop's recorded rounds, ratio, interval, and verdict."""
    results_dir = os.path.join(str(tmpdir), 'results')
    machine_dir = os.path.join(results_dir, 'cheetah')
    os.makedirs(machine_dir)
    util.write_json(
        os.path.join(machine_dir, 'machine.json'),
        {'machine': 'cheetah'},
        api_version=1,
    )
    util.write_json(
        os.path.join(results_dir, 'benchmarks.json'),
        {
            'bench.time_it': {
                'name': 'bench.time_it',
                'params': [],
                'param_names': [],
                'unit': 'seconds',
                'version': '1',
            }
        },
        api_version=2,
    )
    params = {'machine': 'cheetah', 'python': '3.12'}
    benchmark = {'name': 'bench.time_it', 'version': '1', 'params': []}
    parent = 'a' * 40
    head = 'b' * 40

    def add_rounds(commit, sample, env_name, rounds):
        result = Results(params, {}, commit, 1, '3.12', env_name, {})
        for number in range(1, rounds + 1):
            result.add_result(
                benchmark,
                runner.BenchmarkResult(
                    result=[sample],
                    samples=[[sample, sample, sample, sample]],
                    number=[None],
                    errcode=0,
                    stderr='',
                    profile=None,
                ),
                record_samples=True,
                append_samples=number > 1,
                round_number=number,
            )
        return result

    before = add_rounds(parent, 1.0, 'py3', 6)
    after = add_rounds(head, 1.03, 'py3', 6)
    repeated = add_rounds(parent, 1.0, 'py3~aa', 6)
    after.set_comparison(
        parent,
        {'bench.time_it': [[1.03, 1.02, 1.04]]},
        {'bench.time_it': [[1.0, 0.99, 1.01]]},
    )
    before.save(results_dir)
    after.save(results_dir)
    repeated.save(results_dir)

    os.chdir(str(tmpdir))
    conf = config.Config.from_json(
        {
            'results_dir': results_dir,
            'repo': tools.generate_test_repo(str(tmpdir)).path,
            'project': 'asv',
            'environment_type': 'virtualenv',
        }
    )
    worsened, improved = Compare.print_table(
        conf,
        parent,
        head,
        factor=1.1,
        split=False,
        machine='cheetah',
        only_changed=False,
        table_format='latex',
        columns='rounds,ratio,interval,verdict,benchmark',
    )
    text, _err = capsys.readouterr()
    assert worsened
    assert not improved
    assert 'Rounds' in text
    assert 'Interval' in text
    assert 'Verdict' in text
    assert r'6 & \num{1.03} & [\num{1.02}, \num{1.04}] & worse' in text
    assert r'bench.time\_it' in text
    assert text.count(r'bench.time\_it') == 1
