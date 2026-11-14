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


# Machine-readable compare output. Markdown and LaTeX render these records.
COMPARE_SCHEMA_VERSION = 1


def _param_combos(params):
    if not params:
        return [()]
    return list(itertools.product(*params))


def _json_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _json_triple(ci):
    if ci is None:
        return None
    return [_json_number(item) for item in ci[:3]]


def _verdict_name(mark, before, after):
    """
    ``slower``, ``faster``, ``unchanged``, or ``failed``.

    A new failure, a repaired failure, differing benchmark versions,
    and a missing measurement are ``failed``.
    """
    if mark == '+':
        return 'slower'
    if mark == '-':
        return 'faster'
    if mark in ('!', '*', 'x') or _isna(before) or _isna(after):
        return 'failed'
    return 'unchanged'


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


def format_latex_bounds(ci):
    """Format the interval alone as ``[\\num{low}, \\num{high}]``."""
    if ci is None:
        return 'n/a'
    _point, low, high = ci
    return rf'[\num{{{low:.2f}}}, \num{{{high:.2f}}}]'


_VERDICT_TEXT = {
    '+': 'worse',
    '-': 'better',
    ' ': 'unchanged',
    '!': 'failed',
    '*': 'fixed',
    'x': 'incomparable',
}


def format_latex_verdict(mark):
    """Word for the comparison mark. ``+`` is a slowdown."""
    label = '' if mark is None else str(mark)
    text = _VERDICT_TEXT.get(label, label.strip() or 'unchanged')
    return latex_escape(text)


def format_latex_interval(ci, mark=' '):
    """
    Format ``(point, low, high)`` as siunitx numbers.

    The cell is ``\\num{point}\\,[\\num{low}, \\num{high}]``. A missing
    interval is ``n/a``. A significance mark is prefixed when *mark* is set.
    """
    if ci is None:
        body = 'n/a'
    else:
        point, low, high = ci
        body = rf'\num{{{point:.2f}}}\,[\num{{{low:.2f}}}, \num{{{high:.2f}}}]'
    label = '' if mark is None else str(mark).strip()
    if not label:
        return body
    if body == 'n/a':
        return label + ' n/a'
    return label + ' ' + body


_LATEX_COLUMN_CHOICES = (
    'benchmark',
    'params',
    'before',
    'after',
    'ratio',
    'mark',
    'ci',
    'interval',
    'aa',
    'rounds',
    'verdict',
)
# Benchmark and parameters are separate. The mark is the significance
# decision (+, -, ~, !, *, x) that the JSON verdict is named from.
_LATEX_COLUMN_DEFAULT = ('benchmark', 'params', 'before', 'after', 'ratio', 'mark')


def latex_columns(spec):
    """
    Parse a comma-separated ``--columns`` list for the LaTeX table.

    ``params`` is the parameter combination. ``mark`` is the significance
    marker from the same record as the JSON verdict: ``+`` slower, ``-``
    faster, ``~`` insignificant, ``!`` a new failure, ``*`` a fixed
    failure, and ``x`` when versions differ. ``ci`` is the ratio with its
    bootstrap interval. ``interval`` is that interval without the point.
    ``aa`` is the A/A band. ``rounds`` is how many interleaved rounds were
    recorded. ``verdict`` is worse, better, or unchanged.
    """
    if spec is None:
        return _LATEX_COLUMN_DEFAULT
    if isinstance(spec, (list, tuple)):
        names = tuple(spec)
    else:
        names = tuple(part.strip() for part in str(spec).split(',') if part.strip())
    unknown = [name for name in names if name not in _LATEX_COLUMN_CHOICES]
    if not names or unknown:
        choices = ', '.join(_LATEX_COLUMN_CHOICES)
        raise util.UserError(f"Unknown --columns {spec!r}. Choices: {choices}")
    return names


def _latex_colspec(columns):
    spec = []
    for column in columns:
        if column in ('benchmark', 'params', 'verdict'):
            spec.append('l')
        elif column == 'mark':
            spec.append('c')
        else:
            spec.append('r')
    return ''.join(spec)


def _split_ratio_text(ratio):
    """Split ``~1.05 [1.00, 1.10]`` into the point text and the interval."""
    text = ratio.strip()
    tilde = text.startswith('~')
    body = text[1:].strip() if tilde else text
    point = body
    if ' [' in body and body.endswith(']'):
        point, _rest = body.split(' [', 1)
    point = point.strip()
    if tilde:
        point = '~' + point
    return point


def _github_cells(record):
    """Markdown cells for one comparison record."""
    mark = record['mark']
    return [
        mark if str(mark).strip() else ' ',
        human_value(record['before_raw'], record['unit'], err=record['before_err']),
        human_value(record['after_raw'], record['unit'], err=record['after_err']),
        record['ratio_text'],
        record['display_name'],
    ]


def _latex_parts(record):
    """
    LaTeX cells for one comparison record.

    Before, after, ratio, name, and parameters are the schema v1 fields.
    The unit on each median is scaled on that row, including bytes.
    """
    before = record['before']
    after = record['after']
    params = ', '.join(str(item) for item in record['params'])
    # Raw values keep failed (None) distinct from skipped (NaN). The unit
    # and the finite median are the schema v1 fields.
    return {
        'before': format_latex_value(record['before_raw'], before['unit']),
        'after': format_latex_value(record['after_raw'], after['unit']),
        'ratio_full': record['ratio_text'],
        'ratio_point': _split_ratio_text(record['ratio_text']),
        'ratio_number': record['ratio'],
        'mark': record['mark'],
        'ci': record['ci'],
        'aa': record['band'],
        'rounds': record['rounds'],
        'benchmark': latex_escape(record['name']),
        'params': latex_escape(params),
        'display': latex_escape(record['display_name']),
    }


def _public_record(record):
    """Fields of the versioned JSON schema."""
    return {
        'name': record['name'],
        'params': record['params'],
        'machine': record['machine'],
        'environment': record['environment'],
        'before': record['before'],
        'after': record['after'],
        'ratio': record['ratio'],
        'ratio_ci': record['ratio_ci'],
        'rounds': record['rounds'],
        'verdict': record['verdict'],
        'aa_band': record['aa_band'],
    }


def _latex_ratio_number(ratio):
    """Schema ratio, without a significance mark."""
    if ratio is None:
        return 'n/a'
    return rf'\num{{{float(ratio):.2f}}}'


def _latex_row(parts, columns):
    mark_elsewhere = 'mark' in columns or 'verdict' in columns
    cells = []
    for column in columns:
        if column == 'before':
            cells.append(parts['before'])
        elif column == 'after':
            cells.append(parts['after'])
        elif column == 'ratio':
            if mark_elsewhere or 'ci' in columns or 'interval' in columns:
                if 'ci' in columns or 'interval' in columns:
                    text = parts['ratio_point']
                    if text.startswith('~'):
                        text = text[1:].strip()
                    cells.append(format_latex_ratio(' ', text))
                else:
                    cells.append(_latex_ratio_number(parts['ratio_number']))
            else:
                cells.append(format_latex_ratio(parts['mark'], parts['ratio_full']))
        elif column == 'mark':
            label = '' if parts['mark'] is None else str(parts['mark']).strip()
            # A tilde is stored on the ratio text, not in the mark field.
            if not label and str(parts['ratio_full']).lstrip().startswith('~'):
                label = '~'
            cells.append(latex_escape(label))
        elif column == 'ci':
            mark = ' ' if mark_elsewhere else parts['mark']
            cells.append(format_latex_interval(parts['ci'], mark))
        elif column == 'interval':
            cells.append(format_latex_bounds(parts['ci']))
        elif column == 'aa':
            cells.append(format_latex_interval(parts['aa']))
        elif column == 'rounds':
            rounds = parts.get('rounds')
            cells.append('n/a' if rounds is None else str(rounds))
        elif column == 'verdict':
            cells.append(format_latex_verdict(parts['mark']))
        elif column == 'params':
            cells.append(parts['params'])
        elif column == 'benchmark' and 'params' in columns:
            cells.append(parts['benchmark'])
        else:
            cells.append(parts['display'])
    return cells


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
            choices=('github', 'latex', 'json'),
            default='github',
            help="""Output format for the comparison. 'github' (default)
            is a GitHub-flavored markdown table. 'latex' is a booktabs
            tabular. 'json' is schema_version 1, one record per benchmark
            and parameter combination. Markdown and LaTeX are rendered
            from those same records.""",
        )

        parser.add_argument(
            '--columns',
            default=None,
            help="""With --format=latex, comma-separated columns to emit.
            Choices: before, after, ratio, ci, interval, aa, rounds,
            verdict, benchmark. The default is before,after,ratio,benchmark.
            ci is the ratio and its bootstrap interval. interval is the
            bracket alone. rounds is how many interleaved rounds were
            recorded, and verdict is worse, better, or unchanged. ci, interval,
            and aa use siunitx \\num values.""",
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
            columns=args.columns,
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
        columns=None,
    ):
        if columns is not None and table_format != 'latex':
            raise util.UserError("--columns applies to --format=latex")
        columns = latex_columns(columns) if table_format == 'latex' else None
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
            columns=columns,
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
        aa_split=False,
        aa_resultset=None,
        stored_ci=None,
        stored_band=None,
        columns=None,
    ):
        if table_format not in ('github', 'latex', 'json'):
            raise ValueError(f"Unknown 'table_format' {table_format!r}")
        if only_body and table_format != 'latex':
            raise util.UserError("--only-body applies to --format=latex")
        if columns is not None and table_format != 'latex':
            raise util.UserError("--columns applies to --format=latex")
        if table_format == 'latex':
            columns = latex_columns(columns)
        results_1 = {}
        results_2 = {}
        ss_1 = {}
        ss_2 = {}
        versions_1 = {}
        versions_2 = {}
        units = {}
        identities = {}

        benchmarks = Benchmarks.load(conf)

        if commit_names is None:
            commit_names = {}

        from .run import AA_ENV_SUFFIX

        failed_notes = []
        disk_ci = {}
        disk_band = {}
        disk_rounds = {}
        aa_rows = []

        def _env_allowed(env_name):
            if env_names is None:
                return True
            plain = env_name.removesuffix(AA_ENV_SUFFIX)
            return env_name in env_names or plain in env_names

        def _fill_stored(result, comp, machine_env):
            ratio_ci = comp.get('ratio_ci') or {}
            band_map = comp.get('aa_band') or {}
            for key, cis in ratio_ci.items():
                params = result._benchmark_params.get(key) or []
                bands = band_map.get(key)
                if bands is None:
                    bands = [None] * len(cis)
                for name, ci_row, band_row in unroll_result(key, params, cis, bands):
                    disk_ci[(name, machine_env)] = ci_row
                    if key in band_map:
                        disk_band[(name, machine_env)] = band_row

        def _rows_from_disk(commit_hash, other_hash, collect_stored):
            rows = []
            for result in iter_results_for_machine_and_hash(
                conf.results_dir, machine, commit_hash
            ):
                if not _env_allowed(result.env_name):
                    continue
                is_aa = result.env_name.endswith(AA_ENV_SUFFIX)
                env_name = result.env_name.removesuffix(AA_ENV_SUFFIX)
                machine_name = result.params['machine']
                machine_env = f"{machine_name}/{env_name}"
                label = machine_env + (" base'" if is_aa else "")
                for bench_name, rounds in result.failed_rounds.items():
                    for number in rounds:
                        failed_notes.append((bench_name, label, number))
                if not is_aa:
                    for key, rounds in result.round_samples.items():
                        count = len(rounds) if rounds else None
                        params = result.get_result_params(key)
                        for name, _ignored in unroll_result(key, params, None):
                            disk_rounds[(name, machine_env)] = count
                if collect_stored and not is_aa:
                    comp = result.get_comparison(other_hash)
                    if comp:
                        _fill_stored(result, comp, machine_env)
                for key in result.get_all_result_keys():
                    params = result.get_result_params(key)
                    row = (
                        key,
                        params,
                        result.get_result_value(key, params),
                        result.get_result_stats(key, params),
                        result.get_result_samples(key, params),
                        result.benchmark_version.get(key),
                        machine_name,
                        result.env_name,
                    )
                    if is_aa:
                        aa_rows.append(row)
                    else:
                        rows.append(row)
            return rows

        if resultset_1 is None:
            resultset_1 = _rows_from_disk(hash_1, hash_2, False)

        if resultset_2 is None:
            resultset_2 = _rows_from_disk(hash_2, hash_1, True)

        if aa_resultset is None and aa_rows:
            aa_resultset = aa_rows
            if stored_ci is None and not disk_band:
                aa_band = True

        if stored_ci is None and disk_ci:
            stored_ci = disk_ci
        if stored_band is None and disk_band:
            stored_band = disk_band

        machine_env_names = set()

        def _collect(resultset, results, samples_by_name, versions):
            for key, params, value, stats, samples, version, machine, env_name in resultset:
                machine_env_name = f"{machine}/{env_name}"
                machine_env_names.add(machine_env_name)
                unrolled = unroll_result(key, params, value, stats, samples)
                for (name, value, stats, samples), combo in zip(unrolled, _param_combos(params)):
                    slot = (name, machine_env_name)
                    units[slot] = benchmarks.get(key, {}).get('unit')
                    results[slot] = value
                    samples_by_name[slot] = (stats, samples)
                    versions[slot] = version
                    identities[slot] = {
                        'name': key,
                        'params': [_format_param_for_display(item) for item in combo],
                        'machine': machine,
                        'environment': env_name,
                    }

        _collect(resultset_1, results_1, ss_1, versions_1)
        _collect(resultset_2, results_2, ss_2, versions_2)

        # Samples from the repeated base slot (base'), keyed like the base row.
        aa_sample_map = {}
        if aa_resultset is not None:
            from .run import AA_ENV_SUFFIX

            for key, params, value, stats, samples, version, machine, env_name in aa_resultset:
                env_name = env_name.removesuffix(AA_ENV_SUFFIX)
                machine_env_name = f"{machine}/{env_name}"
                for name, _value, _sample_stats, samples in unroll_result(
                    key, params, value, stats, samples
                ):
                    aa_sample_map[(name, machine_env_name)] = samples

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
            row_band = None if stored_band is None else stored_band.get(benchmark)
            if row_band is not None:
                row_band = tuple(row_band)
            suppress_factor = aa_band or aa_split or row_band is not None
            ci_value = None
            band_value = None

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
                if not suppress_factor:
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
                if not suppress_factor:
                    worsened = True
            else:
                color = 'default'
                mark = ' '

                # Mark statistically insignificant results
                if _is_result_better(time_1, time_2, None, None, factor) or _is_result_better(
                    time_2, time_1, None, None, factor
                ):
                    ratio = "~" + ratio.strip()

            has_stored_ci = stored_ci is not None and benchmark in stored_ci
            if (bootstrap_ci or aa_band or aa_split or has_stored_ci) and mark not in (
                'x',
                '!',
                '*',
            ):
                before_samples = ss_1.get(benchmark, (None, None))[1]
                after_samples = ss_2.get(benchmark, (None, None))[1]
                if aa_band:
                    paired = aa_sample_map.get(benchmark)
                    # Missing base' samples must not fall back to the split.
                    paired_arg = paired if paired is not None else []
                else:
                    paired_arg = None
                if has_stored_ci:
                    ci = stored_ci[benchmark]
                    ci = None if ci is None else tuple(ci)
                    band = row_band
                    if ci is None:
                        aa_mark = ' ' if suppress_factor else None
                    elif band is None and not (aa_band or aa_split):
                        aa_mark = None
                    elif band is None or not _stats.ci_outside_band(ci, band):
                        aa_mark = ' '
                    elif ci[0] > 1:
                        aa_mark = '+'
                    elif ci[0] < 1:
                        aa_mark = '-'
                    else:
                        aa_mark = ' '
                else:
                    ci, aa_mark = _stats.interleaved_ratio_decision(
                        before_samples,
                        after_samples,
                        aa_samples=paired_arg,
                        split_aa=aa_split,
                    )
                    if aa_split:
                        band = _stats.split_half_aa_band(before_samples, seed=2)
                    elif aa_band and paired_arg:
                        band = _stats.bootstrap_median_ratio_ci(before_samples, paired_arg, seed=2)
                    else:
                        band = None
                ci_value = ci
                band_value = band
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
            identity = identities.get(benchmark) or {
                'name': benchmark[0],
                'params': [],
                'machine': benchmark[1].split('/', 1)[0],
                'environment': benchmark[1].split('/', 1)[-1],
            }

            if len(machine_env_names) > 1:
                benchmark_name = "{} [{}]".format(*benchmark)
            else:
                benchmark_name = benchmark[0]
            ratio_text = ratio.strip()
            numeric_ratio = None
            if ci_value is not None:
                numeric_ratio = _json_number(ci_value[0])
            elif ratio_text not in ('n/a', '') and not ratio_text.startswith('~'):
                try:
                    numeric_ratio = float(ratio_text)
                except ValueError:
                    numeric_ratio = None
            elif ratio_text.startswith('~'):
                try:
                    numeric_ratio = float(ratio_text[1:].strip().split()[0])
                except ValueError:
                    numeric_ratio = None
            # One record feeds markdown, LaTeX, and JSON.
            record = {
                'name': identity['name'],
                'params': identity['params'],
                'machine': identity['machine'],
                'environment': identity['environment'],
                'before': {'median': _json_number(time_1), 'unit': unit},
                'after': {'median': _json_number(time_2), 'unit': unit},
                'ratio': numeric_ratio,
                'ratio_ci': _json_triple(ci_value),
                'rounds': disk_rounds.get(benchmark),
                'verdict': _verdict_name(mark, time_1, time_2),
                'aa_band': _json_triple(band_value),
                'mark': mark,
                'before_raw': time_1,
                'after_raw': time_2,
                'before_err': err_1,
                'after_err': err_2,
                'unit': unit,
                'ratio_text': ratio_text,
                'display_name': benchmark_name,
                'ci': ci_value,
                'band': band_value,
            }
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
        latex_header_for = {
            'before': _latex_revision_header('Before', hash_1, name_1),
            'after': _latex_revision_header('After', hash_2, name_2),
            'ratio': 'Ratio',
            'ci': 'Ratio CI',
            'interval': 'Interval',
            'aa': 'A/A',
            'rounds': 'Rounds',
            'verdict': 'Verdict',
            'benchmark': 'Benchmark',
            'params': 'Params',
            'mark': 'Mark',
        }
        if columns is None:
            columns = _LATEX_COLUMN_DEFAULT
        latex_headers = [latex_header_for[column] for column in columns]
        wrote_latex_preamble = False

        for key in keys:
            if len(bench[key]) == 0:
                continue

            if sort == 'default':
                pass
            elif sort == 'ratio':
                bench[key].sort(key=lambda v: v['ratio_text'], reverse=True)
            elif sort == 'name':
                bench[key].sort(key=lambda v: _github_cells(v)[2])
            else:
                raise ValueError("Unknown 'sort'")

            if table_format == 'json':
                continue

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
                        [_github_cells(record) for record in bench[key]],
                        headers=github_headers,
                        tablefmt="github",
                    )
                )
            else:
                table = format_latex_tabular(
                    [_latex_row(_latex_parts(record), columns) for record in bench[key]],
                    latex_headers,
                    only_body=only_body,
                    colspec=_latex_colspec(columns),
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

        if table_format == 'json':
            payload = {
                'schema_version': COMPARE_SCHEMA_VERSION,
                'before': {'commit': hash_1, 'label': name_1.strip()},
                'after': {'commit': hash_2, 'label': name_2.strip()},
                'records': [
                    _public_record(record) for key in keys for record in bench.get(key, [])
                ],
            }
            print(json.dumps(payload, indent=2))

        worsened = worsened or aa_worsened
        improved = improved or aa_improved
        for bench_name, env_label, round_number in failed_notes:
            log.warning(f"{bench_name} [{env_label}]: round {round_number} failed")
        return worsened, improved
