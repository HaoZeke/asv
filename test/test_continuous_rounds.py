# Licensed under a 3-clause BSD style license - see LICENSE.rst
import os

import pytest

from asv import _stats, config, runner, util
from asv.commands import make_argparser
from asv.commands.compare import Compare
from asv.commands.continuous import (
    _adaptive_settings,
    _apply_interleave_rounds,
    _store_interleaved_comparisons,
)
from asv.commands.run import (
    AA_ENV_SUFFIX,
    install_commits_once,
    interleaved_slot_order,
    runner_for_interleaved_slot,
    unique_slot_commits,
)
from asv.environment import Environment
from asv.results import Results

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
    assert disabled.aa_band_split is False

    band = parser.parse_args(['continuous', '--aa-band', '--interleave-rounds', '2', 'feature'])
    assert band.aa_band is True
    assert band.aa_band_split is False
    assert band.interleave_round_count == 2

    split = parser.parse_args(['continuous', '--aa-band-split', 'feature'])
    assert split.aa_band is False
    assert split.aa_band_split is True

    adaptive = parser.parse_args(
        [
            'continuous',
            '--interleave-rounds',
            '20',
            '--min-rounds',
            '6',
            '--min-effect',
            '0.03',
            'feature',
        ]
    )
    assert adaptive.interleave_round_count == 20
    assert adaptive.min_rounds == 6
    assert adaptive.min_effect == 0.03
    assert adaptive.branch == 'feature'


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


def test_adaptive_settings_uses_obf_looks():
    min_effect, looks = _adaptive_settings(6, 20, True, None)
    assert looks[0] == 6
    assert looks[-1] == 20
    assert len(looks) == 4
    assert min_effect is None
    assert _adaptive_settings(6, 20, True, 0.03)[0] == 0.03
    assert _stats.obf_alpha(len(looks), len(looks)) == pytest.approx(0.05, abs=1e-4)
    assert _stats.obf_alpha(1, len(looks)) < 0.001
    assert _stats.planned_looks(8, 8) == [8]
    assert _adaptive_settings(None, None, False, None) is None
    with pytest.raises(util.UserError, match='aa-band'):
        _adaptive_settings(6, 20, False, None)
    with pytest.raises(util.UserError, match='interleave-rounds'):
        _adaptive_settings(6, None, True, None)
    with pytest.raises(util.UserError, match='between'):
        _adaptive_settings(21, 20, True, None)
    with pytest.raises(util.UserError, match='min-effect'):
        _adaptive_settings(None, 20, True, 0.03)
    with pytest.raises(util.UserError, match='between 0 and 1'):
        _adaptive_settings(6, 20, True, 0)


def test_adaptive_stop_outside_inside_and_undecided():
    flat = [1.0] * 30
    doubled = [2.0] * 30
    verdict, ci, band = _stats.adaptive_aa_stop(flat, doubled, flat, alpha=0.05)
    assert verdict == 'outside'
    assert _stats.ci_outside_band(ci, band)
    # A precise no-op is inside the band, and an interim look keeps going.
    verdict, ci, band = _stats.adaptive_aa_stop(flat, flat, flat, alpha=0.05, final=False)
    assert verdict is None
    assert _stats.ci_inside_band(ci, band)
    verdict, ci, band = _stats.adaptive_aa_stop(flat, flat, flat, alpha=0.05, final=True)
    assert verdict == 'inside'
    # A wide cloud is not outside the band, so an interim look does not stop.
    noisy = [0.5, 1.5, 0.8, 1.4, 0.6, 1.6, 0.7, 1.3]
    verdict, ci, _band = _stats.adaptive_aa_stop(noisy, noisy, noisy, alpha=0.05)
    assert verdict is None
    assert ci is not None
    # The widened alpha used for repeated looks is stricter than 95%.
    wide = _stats.bootstrap_median_ratio_ci(noisy, [x * 1.2 for x in noisy], alpha=0.05)
    narrow = _stats.bootstrap_median_ratio_ci(
        noisy, [x * 1.2 for x in noisy], alpha=_stats.obf_alpha(1, 4)
    )
    assert (narrow[2] - narrow[1]) > (wide[2] - wide[1])


def test_stopping_rule_noop_early_large_effect_first_look_widened_interval(capsys, tmpdir):
    looks = _stats.planned_looks(6, 20)
    alpha = _stats.obf_alpha(1, len(looks))
    assert looks[0] == 6
    assert _stats.alpha_for_recorded_rounds(looks[0], looks) == alpha
    assert _stats.alpha_for_recorded_rounds(looks[-1], looks) == pytest.approx(0.05, abs=1e-4)
    flat = [1.0] * 40
    doubled = [2.0] * 40
    # A precise no-op overlaps the band, so the first look does not stop.
    verdict, ci, band = _stats.adaptive_aa_stop(flat, flat, flat, alpha=alpha, final=False)
    assert verdict is None
    assert not _stats.ci_outside_band(ci, band)
    verdict, _ci, _band = _stats.adaptive_aa_stop(flat, flat, flat, alpha=alpha, final=True)
    assert verdict == 'inside'
    # A large effect clears the band at that same first-look alpha.
    verdict, ci, band = _stats.adaptive_aa_stop(flat, doubled, flat, alpha=alpha, final=False)
    assert verdict == 'outside'
    assert ci[0] == 2.0

    before = [1.0, 1.4, 0.7, 1.2, 0.8, 1.5, 0.9, 1.3] * 6
    after = [value * 1.2 for value in before]
    nominal = _stats.bootstrap_median_ratio_ci(before, after, alpha=0.05)
    widened = _stats.bootstrap_median_ratio_ci(before, after, alpha=alpha)
    assert widened[1] < nominal[1]
    assert widened[2] > nominal[2]
    assert _stats.format_ratio_ci(widened) != _stats.format_ratio_ci(nominal)

    results_dir = os.path.join(str(tmpdir), 'results')
    machine_dir = os.path.join(results_dir, 'mach')
    os.makedirs(machine_dir)
    util.write_json(os.path.join(machine_dir, 'machine.json'), {'machine': 'mach'}, api_version=1)
    util.write_json(
        os.path.join(results_dir, 'benchmarks.json'),
        {
            'bench.time_it': {
                'name': 'bench.time_it',
                'params': [],
                'param_names': [],
                'version': '1',
                'unit': 'seconds',
            }
        },
        api_version=2,
    )
    params = {'machine': 'mach', 'python': '3.12'}
    benchmark = {'name': 'bench.time_it', 'version': '1', 'params': []}
    parent = 'a' * 40
    head = 'b' * 40

    def store(commit, samples, env_name, round_number=None):
        result = Results(params, {}, commit, 1, '3.12', env_name, {})
        result.add_result(
            benchmark,
            runner.BenchmarkResult(
                result=[samples[len(samples) // 2]],
                samples=[samples],
                number=[None],
                errcode=0,
                stderr='',
                profile=None,
            ),
            record_samples=True,
            round_number=round_number,
        )
        result.save(results_dir)

    store(parent, before, 'py3')
    # Six recorded rounds selects the first planned look, not the nominal 95%.
    store(head, after, 'py3', round_number=looks[0])
    store(parent, before, 'py3~aa')
    os.chdir(str(tmpdir))
    conf = config.Config.from_json(
        {
            'results_dir': results_dir,
            'repo': tools.generate_test_repo(str(tmpdir)).path,
            'project': 'asv',
            'environment_type': 'virtualenv',
            'branches': ['main'],
        }
    )

    class Bench:
        def items(self):
            return [('bench.time_it', benchmark)]

    _store_interleaved_comparisons(
        conf,
        {
            'environments': [type('E', (), {'name': 'py3'})()],
            'machine_params': {'machine': 'mach'},
            'benchmarks': Bench(),
        },
        parent,
        head,
        aa_band=True,
        aa_split=False,
        alpha=0.05,
        looks=looks,
    )
    Compare.print_table(
        conf,
        parent,
        head,
        factor=1.1,
        split=False,
        machine='mach',
        only_changed=False,
    )
    text, _err = capsys.readouterr()
    assert _stats.format_ratio_ci(widened) in text
    assert _stats.format_ratio_ci(nominal) not in text


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


def test_slot_rotation_includes_repeated_base():
    base = ('base', '')
    base_prime = ('base', AA_ENV_SUFFIX)
    head = ('head', '')
    slots = [base, base_prime, head]
    assert interleaved_slot_order(slots, 1) == [base, base_prime, head]
    assert interleaved_slot_order(slots, 2) == [base_prime, head, base]
    assert interleaved_slot_order(slots, 3) == [head, base, base_prime]
    assert interleaved_slot_order(slots, 4) == interleaved_slot_order(slots, 1)


def test_aa_decision_on_deterministic_samples():
    flat = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    doubled = [2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0]
    halved = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
    drifted = [1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 1.5]

    ci, mark = _stats.interleaved_ratio_decision(flat, flat, aa_samples=flat)
    assert ci == (1.0, 1.0, 1.0)
    assert mark == ' '

    ci, mark = _stats.interleaved_ratio_decision(flat, doubled, aa_samples=flat)
    assert ci == (2.0, 2.0, 2.0)
    assert mark == '+'

    ci, mark = _stats.interleaved_ratio_decision(flat, halved, aa_samples=flat)
    assert ci == (0.5, 0.5, 0.5)
    assert mark == '-'

    # Head matches the repeated base, so the CI sits on the A/A band.
    ci, mark = _stats.interleaved_ratio_decision(flat, drifted, aa_samples=drifted)
    assert ci == (1.5, 1.5, 1.5)
    assert mark == ' '
    # The half-split of a constant base is a point at 1 and would flag it.
    ci, mark = _stats.interleaved_ratio_decision(flat, drifted, split_aa=True)
    assert mark == '+'

    # Without an A/A band the CI is reported and the mark is left alone.
    ci, mark = _stats.interleaved_ratio_decision(flat, doubled)
    assert ci == (2.0, 2.0, 2.0)
    assert mark is None
    assert _stats.format_ratio_ci(ci) == '2.00 [2.00, 2.00]'


def _result_row(value, samples, env_name='py3'):
    stats = {
        'q_25': value,
        'q_75': value,
        'repeat': len(samples),
        'ci_99_a': value,
        'ci_99_b': value,
    }
    return ('bench.time_it', [], [value], [stats], [samples], '1', 'cheetah', env_name)


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
        aa_resultset=[_result_row(1.0, flat, env_name='py3' + AA_ENV_SUFFIX)],
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


def test_interim_inside_band_does_not_stop():
    """An interval inside the A/A band at an interim look keeps running."""
    before = [1.0, 1.4, 0.7, 1.2, 0.8, 1.5, 0.9, 1.3] * 6
    after = [value * 1.02 for value in before]
    looks = _stats.planned_looks(6, 20)
    alpha = _stats.obf_alpha(1, len(looks))
    verdict, ci, band = _stats.adaptive_aa_stop(
        before, after, before, alpha=alpha, final=False
    )
    assert ci is not None and band is not None
    assert not _stats.ci_outside_band(ci, band)
    assert verdict is None
    # The same samples are a no-change only when this is the last look.
    final_verdict, _ci, _band = _stats.adaptive_aa_stop(
        before, after, before, alpha=_stats.obf_alpha(len(looks), len(looks)), final=True
    )
    assert final_verdict == 'inside'
    # Opt-in: stop early only when the whole interval is inside 1 ± threshold.
    contained, contained_ci, _band = _stats.adaptive_aa_stop(
        [1.0] * 40, [1.0] * 40, [1.0] * 40, alpha=0.05, min_effect=0.03, final=False
    )
    assert contained == 'inside'
    assert _stats.ci_within_threshold(contained_ci, 0.03)
    spilled, spilled_ci, _band = _stats.adaptive_aa_stop(
        before, after, before, alpha=alpha, min_effect=0.01, final=False
    )
    assert spilled is None
    assert not _stats.ci_within_threshold(spilled_ci, 0.01)


def test_interleaved_rounds_do_not_rebuild_when_install_is_ready(tmpdir):
    commit_base = 'a' * 40
    commit_head = 'b' * 40
    slots = [(commit_base, ''), (commit_base, AA_ENV_SUFFIX), (commit_head, '')]
    env = Environment.__new__(Environment)
    env._path = os.path.join(str(tmpdir), 'env')
    os.makedirs(env._path)
    env._repo_subdir = None
    env._install_timeout = 0
    env._project = 'accum'
    env._build_command = None
    env._install_command = None
    env._uninstall_command = None
    env._global_env_vars = {}
    env._cache = None
    env.tool_name = 'virtualenv'
    env._python = '3.12'
    env._requirements = {}
    env._tagged_env_vars = {}
    env.installs = []

    def forbid_install(conf, repo, commit_hash):
        env.installs.append(commit_hash)
        raise AssertionError(commit_hash)

    env.install_project = forbid_install
    checksum = env._get_install_checksum()
    for commit in (commit_base, commit_head):
        path = env.retained_env_path(commit)
        os.makedirs(path)
        util.write_json(
            os.path.join(path, 'asv-install-status.json'),
            {'commit_hash': commit, 'install_checksum': checksum},
            api_version=1,
        )

    commits = unique_slot_commits(slots)
    retained, _durations = install_commits_once(env, None, None, commits)
    assert env.installs == []
    assert list(retained) == [commit_base, commit_head]
    for number in range(1, 5):
        for commit, _suffix in interleaved_slot_order(slots, number):
            runner = runner_for_interleaved_slot(env, commit, retained)
            assert runner is not None
            assert runner is not env
            assert runner._path == env.retained_env_path(commit)
    install_commits_once(env, None, None, commits)
    assert env.installs == []
