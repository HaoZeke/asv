# Licensed under a 3-clause BSD style license - see LICENSE.rst

import itertools
import json
import math

import tabulate
from asv_runner.console import color_print
from asv_runner.statistics import get_err
from asv_runner.util import _human_time_units, human_float

from .. import _stats, util
from ..benchmarks import Benchmarks
from ..console import log
from ..environment import get_environments
from ..machine import iter_machine_files
from ..repo import NoSuchNameError, get_repo
from ..results import iter_results_for_machine_and_hash
from ..util import human_value, load_json
from . import Command, common_args


def mean(values):
    values = [value for value in values if value is not None]
    if values:
        return sum(values) / float(len(values))
    else:
        return None


def _format_param_for_display(param):
    """
    Format a benchmark parameter for single-line display (e.g. compare tables).

    Parameter ``repr`` values can contain newlines (common for rich objects),
    which break markdown/github tables produced by ``tabulate`` (#1393).
    """
    text = param if isinstance(param, str) else repr(param)
    return ' '.join(text.split())


def unroll_result(benchmark_name, params, *values):
    """
    Iterate through parameterized result values

    Yields
    ------
    name
        Strings of the form "benchmark_name(value1, value2)" with
        parameter values substituted in. For non-parameterized
        results, simply the benchmark name.
    value
        Benchmark timing or other scalar value.

    """
    num_comb = 1
    for p in params:
        num_comb *= len(p)

    values = list(values)
    for j in range(len(values)):
        if values[j] is None:
            values[j] = [None] * num_comb

    for params, value in zip(itertools.product(*params), zip(*values)):
        if params == ():
            name = benchmark_name
        else:
            formatted = [_format_param_for_display(p) for p in params]
            name = f"{benchmark_name}({', '.join(formatted)})"
        yield (name,) + value


def _isna(value):
    # None (failed) or NaN (skipped)
    return value is None or value != value


def _is_result_better(a, b, a_ss, b_ss, factor, use_stats=True):
    """
    Check if result 'a' is better than 'b' by the given factor,
    possibly taking confidence intervals into account.

    """

    if (
        use_stats
        and a_ss
        and b_ss
        and a_ss[0]
        and b_ss[0]
        and (a_ss[0].get('repeat', 0) != 1 and b_ss[0].get('repeat', 0) != 1)
    ):
        # Return False if estimates don't differ.
        #
        # Special-case the situation with only one sample, in which
        # case we do the comparison only based on `factor` as there's
        # not enough data to do statistics.
        if not _stats.is_different(a_ss[1], b_ss[1], a_ss[0], b_ss[0]):
            return False

    return a < b / factor


# LaTeX text-mode specials. Backslash is mapped to a command that itself
# contains braces, so replacement is per character rather than sequential.
_LATEX_ESCAPES = {
    '\\': r'\textbackslash{}',
    '&': r'\&',
    '%': r'\%',
    '#': r'\#',
    '$': r'\$',
    '_': r'\_',
    '{': r'\{',
    '}': r'\}',
    '~': r'\textasciitilde{}',
    '^': r'\textasciicircum{}',
}

# Suffixes from asv_runner human_time that siunitx provides as unit macros.
_TIME_UNIT_MACROS = {
    'ns': r'\nano\second',
    'μs': r'\micro\second',
    'ms': r'\milli\second',
    's': r'\second',
    'm': r'\minute',
    'h': r'\hour',
    'd': r'\day',
}

# human_file_size scales by 1000**n with suffixes ' kMGTPEH'.
_BYTE_UNIT_MACROS = (
    r'\byte',
    r'\kilo\byte',
    r'\mega\byte',
    r'\giga\byte',
    r'\tera\byte',
    r'\peta\byte',
    r'\exa\byte',
    r'\zetta\byte',
)


def latex_escape(text):
    """
    Escape LaTeX text-mode specials in *text*.

    The characters ``\\ & % # $ _ { } ~ ^`` are escaped. Other characters,
    including spaces produced by parameter formatting, are left unchanged.
    """
    return ''.join(_LATEX_ESCAPES.get(ch, ch) for ch in str(text))


def _siunitx_qty(number, unit_macro):
    return rf'\qty{{{number}}}{{{unit_macro}}}'


def _format_time_latex(seconds, err):
    """Format a duration with the same scaling as ``human_time``."""
    units = _human_time_units
    seconds = float(seconds)
    scale = seconds

    if scale == 0 and err is not None:
        scale = float(err)

    if scale == 0:
        units = (('s', 1), ('m', 60))

    if scale != scale:
        return 'n/a'

    for i in range(len(units) - 1):
        if scale < units[i + 1][1]:
            suffix, div = units[i]
            number = human_float(seconds / div, 3, significant_zeros=True)
            if err is not None:
                err_text = human_float(err / div, 1, truncate_small=2)
                number = f'{number} \\pm {err_text}'
            macro = _TIME_UNIT_MACROS.get(suffix)
            if macro:
                return _siunitx_qty(number, macro)
            return rf'\num{{{number}}}\,\text{{{latex_escape(suffix)}}}'

    # human_time falls through to this for durations beyond the largest unit.
    return r'\textasciitilde{}0'


def _format_bytes_latex(size, err):
    """Format a byte count with the same scaling as ``human_file_size``."""
    size = float(size)
    if size < 1:
        size = 0.0

    if size == 0:
        num_scale = 0
    else:
        num_scale = math.floor(math.log(size) / math.log(1000))

    if num_scale > 7:
        number = human_float(size, 3)
        if err is not None:
            err_text = human_float(err, 1, truncate_small=2)
            number = f'{number} \\pm {err_text}'
        return rf'\num{{{number}}}\,\text{{?}}'

    scale = int(math.pow(1000, num_scale))
    number = human_float(size / scale, 3)
    if err is not None:
        err_text = human_float(err / scale, 1, truncate_small=2)
        number = f'{number} \\pm {err_text}'
    return _siunitx_qty(number, _BYTE_UNIT_MACROS[num_scale])


def _format_generic_latex(value, err, unit):
    """Format a unitless or custom-unit number for siunitx."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return latex_escape(human_value(value, unit, err=err))
    if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
        return latex_escape(human_value(value, unit, err=err))

    if isinstance(value, int):
        number = str(value)
    else:
        number = json.dumps(value)
    if err is not None:
        number = f'{number} \\pm {err:.2g}'

    body = rf'\num{{{number}}}'
    if unit and unit != 'unit':
        body += rf'\,\text{{{latex_escape(unit)}}}'
    return body


def format_latex_value(value, unit, err=None):
    """
    Format one measurement the way ``human_value`` does, as LaTeX.

    Times and byte counts use ``\\qty`` with siunitx unit macros. Other
    numeric results use ``\\num``. Failed and missing values stay ``failed``
    and ``n/a``, matching the text table.
    """
    if value is None:
        return 'failed'
    if isinstance(value, float) and math.isnan(value):
        return 'n/a'
    if unit == 'seconds' and isinstance(value, (int, float)) and not isinstance(value, bool):
        return _format_time_latex(value, err)
    if unit == 'bytes' and isinstance(value, (int, float)) and not isinstance(value, bool):
        return _format_bytes_latex(value, err)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _format_generic_latex(value, err, unit)
    return latex_escape(human_value(value, unit, err=err))


def format_latex_ratio(mark, ratio):
    """
    Format the ratio cell, keeping the text table's significance marks.

    ``+`` and ``-`` are the change column. ``~`` is the insignificant-ratio
    prefix and is emitted as ``\\textasciitilde{}``. ``!``, ``*``, and ``x``
    are kept as well.
    """
    ratio = ratio.strip()
    tilde = ratio.startswith('~')
    if tilde:
        ratio = ratio[1:].strip()

    if ratio == 'n/a':
        body = 'n/a'
    elif ' [' in ratio and ratio.endswith(']'):
        point, interval = ratio.split(' [', 1)
        body = rf'\num{{{point}}} [{interval}'
    else:
        body = rf'\num{{{ratio}}}'

    parts = []
    if mark.strip():
        parts.append(latex_escape(mark.strip()))
    if tilde:
        parts.append(r'\textasciitilde{}')
    if not parts:
        return body
    # The text table glues "~" to the number and leaves +/- in their own column.
    if tilde and not mark.strip():
        return parts[0] + body
    return ' '.join(parts) + ' ' + body


def format_latex_tabular(rows, headers, only_body=False, colspec='rrrl'):
    """
    Render *rows* as a booktabs tabular.

    Each row is a sequence of already formatted cell strings. *colspec*
    is the tabular column specification; the comparison table uses
    ``rrrl``. With *only_body*, return only the ``\\\\``-terminated lines.
    """
    lines = []
    if not only_body:
        lines.append(rf'\begin{{tabular}}{{{colspec}}}')
        lines.append(r'\toprule')
        lines.append(' & '.join(headers) + r' \\')
        lines.append(r'\midrule')
    for row in rows:
        lines.append(' & '.join(row) + r' \\')
    if not only_body:
        lines.append(r'\bottomrule')
        lines.append(r'\end{tabular}')
    return '\n'.join(lines)


def _latex_revision_header(label, commit_hash, commit_name):
    header = rf'{label} [\texttt{{{latex_escape(commit_hash[:8])}}}]'
    if commit_name:
        header += rf' \texttt{{{latex_escape(commit_name)}}}'
    return header


class Compare(Command):
    @classmethod
    def setup_arguments(cls, subparsers):
        parser = subparsers.add_parser(
            "compare",
            help="""Compare the benchmark results between two revisions
                    (averaged over configurations)""",
            description="Compare two sets of results",
        )

        parser.add_argument('revision1', help="""The reference revision.""")

        parser.add_argument('revision2', help="""The revision being compared.""")

        common_args.add_compare(parser, sort_default='default', only_changed_default=False)

        parser.add_argument(
            '--machine',
            '-m',
            type=str,
            default=None,
            help="""The machine to compare the revisions for.""",
        )

        parser.add_argument(
            '--format',
            dest='table_format',
            choices=('github', 'latex'),
            default='github',
            help="""Output format for the comparison table. 'github'
            (default) is a GitHub-flavored markdown table. 'latex' is a
            booktabs tabular with before, after, ratio, and benchmark
            columns.""",
        )

        parser.add_argument(
            '--only-body',
            action='store_true',
            help="""With --format=latex, emit only the tabular body:
            one row per benchmark, without the tabular environment,
            column headings, or booktabs rules.""",
        )

        common_args.add_environment(parser)

        parser.set_defaults(func=cls.run_from_args)

        return parser

    @classmethod
    def run_from_conf_args(cls, conf, args):
        return cls.run(
            conf=conf,
            hash_1=args.revision1,
            hash_2=args.revision2,
            factor=args.factor,
            split=args.split,
            only_changed=args.only_changed,
            sort=args.sort,
            machine=args.machine,
            env_spec=args.env_spec,
            use_stats=args.use_stats,
            table_format=args.table_format,
            only_body=args.only_body,
        )

    @classmethod
    def run(
        cls,
        conf,
        hash_1,
        hash_2,
        factor=None,
        split=False,
        only_changed=False,
        sort='default',
        machine=None,
        env_spec=None,
        use_stats=True,
        table_format='github',
        only_body=False,
    ):
        repo = get_repo(conf)
        try:
            hash_1 = repo.get_hash_from_name(hash_1)
        except NoSuchNameError:
            pass

        try:
            hash_2 = repo.get_hash_from_name(hash_2)
        except NoSuchNameError:
            pass

        if env_spec:
            env_names = [
                env.name for env in get_environments(conf, env_spec, verbose=False)
            ] + list(env_spec)
        else:
            env_names = None

        machines = []
        for path in iter_machine_files(conf.results_dir):
            d = load_json(path)
            machines.append(d['machine'])

        if len(machines) == 0:
            raise util.UserError("No results found")
        elif machine is None:
            if len(machines) > 1:
                raise util.UserError(
                    "Results available for several machines: {} - "
                    "specify which one to use with the --machine option".format('/'.join(machines))
                )
            else:
                machine = machines[0]
        elif machine not in machines:
            raise util.UserError(f"Results for machine '{machine} not found")

        commit_names = {
            hash_1: repo.get_name_from_hash(hash_1),
            hash_2: repo.get_name_from_hash(hash_2),
        }

        cls.print_table(
            conf,
            hash_1,
            hash_2,
            factor=factor,
            split=split,
            use_stats=use_stats,
            only_changed=only_changed,
            sort=sort,
            machine=machine,
            env_names=env_names,
            commit_names=commit_names,
            table_format=table_format,
            only_body=only_body,
        )

    @classmethod
    def print_table(
        cls,
        conf,
        hash_1,
        hash_2,
        factor,
        split,
        resultset_1=None,
        resultset_2=None,
        machine=None,
        only_changed=False,
        sort='default',
        use_stats=True,
        env_names=None,
        commit_names=None,
        table_format='github',
        only_body=False,
        bootstrap_ci=False,
        aa_band=False,
    ):
        if table_format not in ('github', 'latex'):
            raise ValueError(f"Unknown 'table_format' {table_format!r}")
        if only_body and table_format != 'latex':
            raise util.UserError("--only-body applies to --format=latex")
        results_1 = {}
        results_2 = {}
        ss_1 = {}
        ss_2 = {}
        versions_1 = {}
        versions_2 = {}
        units = {}

        benchmarks = Benchmarks.load(conf)

        if commit_names is None:
            commit_names = {}

        def results_default_iter(commit_hash):
            for result in iter_results_for_machine_and_hash(
                conf.results_dir, machine, commit_hash
            ):
                if env_names is not None and result.env_name not in env_names:
                    continue
                for key in result.get_all_result_keys():
                    params = result.get_result_params(key)
                    result_value = result.get_result_value(key, params)
                    result_stats = result.get_result_stats(key, params)
                    result_samples = result.get_result_samples(key, params)
                    result_version = result.benchmark_version.get(key)
                    yield (
                        key,
                        params,
                        result_value,
                        result_stats,
                        result_samples,
                        result_version,
                        result.params['machine'],
                        result.env_name,
                    )

        if resultset_1 is None:
            resultset_1 = results_default_iter(hash_1)

        if resultset_2 is None:
            resultset_2 = results_default_iter(hash_2)

        machine_env_names = set()

        for key, params, value, stats, samples, version, machine, env_name in resultset_1:
            machine_env_name = f"{machine}/{env_name}"
            machine_env_names.add(machine_env_name)
            for name, value, stats, samples in unroll_result(key, params, value, stats, samples):
                units[(name, machine_env_name)] = benchmarks.get(key, {}).get('unit')
                results_1[(name, machine_env_name)] = value
                ss_1[(name, machine_env_name)] = (stats, samples)
                versions_1[(name, machine_env_name)] = version

        for key, params, value, stats, samples, version, machine, env_name in resultset_2:
            machine_env_name = f"{machine}/{env_name}"
            machine_env_names.add(machine_env_name)
            for name, value, stats, samples in unroll_result(key, params, value, stats, samples):
                units[(name, machine_env_name)] = benchmarks.get(key, {}).get('unit')
                results_2[(name, machine_env_name)] = value
                ss_2[(name, machine_env_name)] = (stats, samples)
                versions_2[(name, machine_env_name)] = version

        if len(results_1) == 0:
            raise util.UserError(f"Did not find results for commit {hash_1}")

        if len(results_2) == 0:
            raise util.UserError(f"Did not find results for commit {hash_2}")

        benchmarks_1 = set(results_1.keys())
        benchmarks_2 = set(results_2.keys())

        joint_benchmarks = sorted(benchmarks_1 | benchmarks_2)

        bench = {}

        if split:
            bench['green'] = []
            bench['red'] = []
            bench['lightgrey'] = []
            bench['default'] = []
        else:
            bench['all'] = []

        worsened = False
        improved = False
        aa_worsened = False
        aa_improved = False

        for benchmark in joint_benchmarks:
            if benchmark in results_1:
                time_1 = results_1[benchmark]
            else:
                time_1 = math.nan

            if benchmark in results_2:
                time_2 = results_2[benchmark]
            else:
                time_2 = math.nan

            if benchmark in ss_1 and ss_1[benchmark][0]:
                err_1 = get_err(time_1, ss_1[benchmark][0])
            else:
                err_1 = None

            if benchmark in ss_2 and ss_2[benchmark][0]:
                err_2 = get_err(time_2, ss_2[benchmark][0])
            else:
                err_2 = None

            version_1 = versions_1.get(benchmark)
            version_2 = versions_2.get(benchmark)

            if _isna(time_1) or _isna(time_2):
                ratio = 'n/a'
                ratio_num = 1e9
            else:
                try:
                    ratio_num = time_2 / time_1
                    ratio = f"{ratio_num:6.2f}"
                except ZeroDivisionError:
                    ratio_num = 1e9
                    ratio = "n/a"

            if version_1 is not None and version_2 is not None and version_1 != version_2:
                # not comparable
                color = 'lightgrey'
                mark = 'x'
            elif time_1 is not None and time_2 is None:
                # introduced a failure
                color = 'red'
                mark = '!'
                worsened = True
            elif time_1 is None and time_2 is not None:
                # fixed a failure
                color = 'green'
                mark = '*'
                improved = True
            elif time_1 is None and time_2 is None:
                # both failed
                color = 'default'
                mark = ' '
            elif _isna(time_1) or _isna(time_2):
                # either one was skipped
                color = 'default'
                mark = ' '
            elif _is_result_better(
                time_2,
                time_1,
                ss_2.get(benchmark),
                ss_1.get(benchmark),
                factor,
                use_stats=use_stats,
            ):
                color = 'green'
                mark = '-'
                if not aa_band:
                    improved = True
            elif _is_result_better(
                time_1,
                time_2,
                ss_1.get(benchmark),
                ss_2.get(benchmark),
                factor,
                use_stats=use_stats,
            ):
                color = 'red'
                mark = '+'
                if not aa_band:
                    worsened = True
            else:
                color = 'default'
                mark = ' '

                # Mark statistically insignificant results
                if _is_result_better(time_1, time_2, None, None, factor) or _is_result_better(
                    time_2, time_1, None, None, factor
                ):
                    ratio = "~" + ratio.strip()

            if (bootstrap_ci or aa_band) and mark not in ('x', '!', '*'):
                before_samples = ss_1.get(benchmark, (None, None))[1]
                after_samples = ss_2.get(benchmark, (None, None))[1]
                ci, aa_mark = _stats.interleaved_ratio_decision(
                    before_samples,
                    after_samples,
                    use_aa_band=aa_band,
                )
                if ci is not None:
                    shown = _stats.format_ratio_ci(ci)
                    tilde = ratio.startswith('~') and aa_mark is None
                    ratio = ('~' if tilde else '') + shown
                if aa_mark == '+':
                    color = 'red'
                    mark = '+'
                    aa_worsened = True
                elif aa_mark == '-':
                    color = 'green'
                    mark = '-'
                    aa_improved = True
                elif aa_mark == ' ':
                    color = 'default'
                    mark = ' '
                    ratio = ratio.removeprefix('~')

            if only_changed and mark in (' ', 'x', '*'):
                continue

            unit = units[benchmark]

            if len(machine_env_names) > 1:
                benchmark_name = "{} [{}]".format(*benchmark)
            else:
                benchmark_name = benchmark[0]
            # Build cells explicitly so a ratio CI ("1.05 [0.98, 1.12]")
            # stays in the ratio column.
            mark_cell = mark if mark.strip() else ' '
            split_line = [
                mark_cell,
                human_value(time_1, unit, err=err_1),
                human_value(time_2, unit, err=err_2),
                ratio.strip(),
                benchmark_name,
            ]
            latex_row = (
                format_latex_value(time_1, unit, err_1),
                format_latex_value(time_2, unit, err_2),
                format_latex_ratio(mark, ratio),
                latex_escape(benchmark_name),
            )
            # Keep the text cells alongside the LaTeX row so --sort still
            # orders both formats the way the markdown table always has.
            record = (split_line, latex_row)
            if split:
                bench[color].append(record)
            else:
                bench['all'].append(record)

        if split:
            keys = ['green', 'default', 'red', 'lightgrey']
        else:
            keys = ['all']

        titles = {}
        titles['green'] = "Benchmarks that have improved:"
        titles['default'] = "Benchmarks that have stayed the same:"
        titles['red'] = "Benchmarks that have got worse:"
        titles['lightgrey'] = "Benchmarks that are not comparable:"
        titles['all'] = "All benchmarks:"

        log.flush()

        name_1 = commit_names.get(hash_1)
        if name_1:
            name_1 = f'<{name_1}>'
        else:
            name_1 = ''

        name_2 = commit_names.get(hash_2)
        if name_2:
            name_2 = f'<{name_2}>'
        else:
            name_2 = ''

        github_headers = [
            'Change',
            f'Before [{hash_1[:8]}] {name_1}',
            f'After [{hash_2[:8]}] {name_2}',
            'Ratio',
            'Benchmark (Parameter)',
        ]
        latex_headers = [
            _latex_revision_header('Before', hash_1, name_1),
            _latex_revision_header('After', hash_2, name_2),
            'Ratio',
            'Benchmark',
        ]
        wrote_latex_preamble = False

        for key in keys:
            if len(bench[key]) == 0:
                continue

            if sort == 'default':
                pass
            elif sort == 'ratio':
                bench[key].sort(key=lambda v: v[0][3], reverse=True)
            elif sort == 'name':
                bench[key].sort(key=lambda v: v[0][2])
            else:
                raise ValueError("Unknown 'sort'")

            if table_format == 'github':
                # In --split mode always show section titles (including with
                # --only-changed); non-split --only-changed keeps a flat table
                # without the "All benchmarks:" banner (issue #885).
                if split or not only_changed:
                    color_print("")
                    color_print(titles[key])
                    color_print("")

                print(
                    tabulate.tabulate(
                        [record[0] for record in bench[key]],
                        headers=github_headers,
                        tablefmt="github",
                    )
                )
            else:
                table = format_latex_tabular(
                    [record[1] for record in bench[key]],
                    latex_headers,
                    only_body=only_body,
                )
                if only_body:
                    # Comments keep split groups distinct inside one tabular.
                    if split:
                        print(f'% {titles[key]}')
                    print(table)
                else:
                    if not wrote_latex_preamble:
                        print(r'% Requires \usepackage{booktabs,siunitx}')
                        wrote_latex_preamble = True
                    if split or not only_changed:
                        print()
                        print(r'\noindent')
                        print(r'\textbf{' + latex_escape(titles[key]) + '}')
                        print()
                    print(table)

        if aa_band:
            worsened = worsened or aa_worsened
            improved = improved or aa_improved
        return worsened, improved
