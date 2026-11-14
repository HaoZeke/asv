# Licensed under a 3-clause BSD style license - see LICENSE.rst
import os

import pytest

from asv import _stats, config, util
from asv.commands import make_argparser
from asv.commands.compare import Compare
from asv.commands.continuous import _apply_interleave_rounds

from . import tools


def test_interleave_rounds_parsing():
    parser, _subparsers = make_argparser()

    bare = parser.parse_args(['continuous', '--interleave-rounds', 'feature'])
    assert bare.interleave_rounds is True
    assert bare.interleave_round_count is None
    assert bare.base is None
    assert bare.branch == 'feature'

    counted = parser.parse_args(['continuous', '--interleave-rounds', '4', 'main', 'feature'])
    assert counted.interleave_rounds is None
    assert counted.interleave_round_count == 4
    assert counted.base == 'main'
    assert counted.branch == 'feature'

    equals = parser.parse_args(['continuous', '--interleave-rounds=3', 'feature'])
    assert equals.interleave_round_count == 3
    assert equals.branch == 'feature'

    disabled = parser.parse_args(['continuous', '--no-interleave-rounds', 'feature'])
    assert disabled.interleave_rounds is False
    assert disabled.aa_band is False

    band = parser.parse_args(['continuous', '--aa-band', '--interleave-rounds', '2', 'feature'])
    assert band.aa_band is True
    assert band.interleave_round_count == 2


def test_apply_interleave_rounds_sets_rounds_attribute():
    flag, attribute, report_ci = _apply_interleave_rounds(4, {'repeat': 2}, False, False)
    assert flag is True
    assert attribute == {'repeat': 2, 'rounds': 4}
    assert report_ci is True

    flag, attribute, report_ci = _apply_interleave_rounds(True, None, False, False)
    assert flag is True
    assert attribute is None
    assert report_ci is False

    flag, attribute, report_ci = _apply_interleave_rounds(None, None, False, True)
    assert flag is True
    assert report_ci is True

    with pytest.raises(util.UserError, match='positive'):
        _apply_interleave_rounds(0, None, False, False)
    with pytest.raises(util.UserError, match='quick'):
        _apply_interleave_rounds(3, None, True, False)


def test_bootstrap_ci_is_deterministic_for_constant_samples():
    before = [1.0, 1.0, 1.0, 1.0]
    after = [2.0, 2.0, 2.0, 2.0]
    first = _stats.bootstrap_median_ratio_ci(before, after, n_resamples=100, seed=1)
    second = _stats.bootstrap_median_ratio_ci(before, after, n_resamples=100, seed=1)
    assert first == (2.0, 2.0, 2.0)
    assert second == first
    assert _stats.aa_ratio_band(before, n_resamples=50, seed=1) == (1.0, 1.0, 1.0)
    assert _stats.bootstrap_median_ratio_ci([1.0], after) is None
    assert _stats.aa_ratio_band([1.0, 1.0, 1.0]) is None


def test_ci_outside_band():
    assert _stats.ci_outside_band((1.2, 1.1, 1.3), (1.0, 0.9, 1.05))
    assert not _stats.ci_outside_band((1.2, 1.0, 1.3), (1.0, 0.9, 1.05))
    # Touching the band is not entirely outside it.
    assert not _stats.ci_outside_band((1.2, 1.05, 1.3), (1.0, 0.9, 1.05))


def test_aa_decision_on_deterministic_samples():
    flat = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    doubled = [2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0]
    halved = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]

    ci, mark = _stats.interleaved_ratio_decision(flat, flat, use_aa_band=True)
    assert ci == (1.0, 1.0, 1.0)
    assert mark == ' '

    ci, mark = _stats.interleaved_ratio_decision(flat, doubled, use_aa_band=True)
    assert ci == (2.0, 2.0, 2.0)
    assert mark == '+'

    ci, mark = _stats.interleaved_ratio_decision(flat, halved, use_aa_band=True)
    assert ci == (0.5, 0.5, 0.5)
    assert mark == '-'

    # Without the A/A band the CI is reported and the mark is left alone.
    ci, mark = _stats.interleaved_ratio_decision(flat, doubled, use_aa_band=False)
    assert ci == (2.0, 2.0, 2.0)
    assert mark is None
    assert _stats.format_ratio_ci(ci) == '2.00 [2.00, 2.00]'


def _result_row(value, samples):
    stats = {
        'q_25': value,
        'q_75': value,
        'repeat': len(samples),
        'ci_99_a': value,
        'ci_99_b': value,
    }
    return ('bench.time_it', [], [value], [stats], [samples], '1', 'cheetah', 'py3')


def test_print_table_aa_band_uses_samples(capsys, tmpdir):
    results_dir = os.path.join(str(tmpdir), 'results')
    os.makedirs(results_dir)
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
    os.chdir(str(tmpdir))
    conf = config.Config.from_json(
        {
            'results_dir': results_dir,
            'repo': tools.generate_test_repo(str(tmpdir)).path,
            'project': 'asv',
            'environment_type': "shouldn't matter what",
        }
    )
    flat = [1.0] * 8
    doubled = [2.0] * 8

    worsened, improved = Compare.print_table(
        conf,
        'a' * 40,
        'b' * 40,
        factor=1.1,
        split=False,
        resultset_1=[_result_row(1.0, flat)],
        resultset_2=[_result_row(2.0, doubled)],
        bootstrap_ci=True,
        aa_band=True,
    )
    text, _err = capsys.readouterr()
    assert worsened
    assert not improved
    assert '2.00 [2.00, 2.00]' in text
    assert (
        '| +' in text
        or text.splitlines()[-2].lstrip().startswith('+')
        or '| +' in text.replace(' ', '')
    )
    # The change cell is a plus. tabulate keeps it in the first column.
    assert '+' in text.split('bench.time_it')[0]

    worsened, improved = Compare.print_table(
        conf,
        'a' * 40,
        'b' * 40,
        factor=1.1,
        split=False,
        resultset_1=[_result_row(1.0, flat)],
        resultset_2=[_result_row(1.0, flat)],
        bootstrap_ci=True,
        aa_band=True,
        only_changed=False,
    )
    text, _err = capsys.readouterr()
    assert not worsened
    assert not improved
    assert '1.00 [1.00, 1.00]' in text
    data_row = next(line for line in text.splitlines() if 'bench.time_it' in line)
    assert data_row.split('|')[1].strip() == ''
