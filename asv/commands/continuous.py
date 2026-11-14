# Licensed under a 3-clause BSD style license - see LICENSE.rst

import argparse
import os

from asv_runner.console import color_print

from .. import _stats, results, util
from ..console import log
from ..repo import NoSuchNameError, get_repo
from . import Command, common_args
from .compare import Compare, unroll_result
from .run import AA_ENV_SUFFIX, Run


def _rewrite_interleave_rounds(args):
    """
    Turn ``--interleave-rounds N`` into a hidden integer option.

    A bare ``--interleave-rounds`` stays a flag so it does not swallow
    the following branch name.
    """
    rewritten = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == '--interleave-rounds' and index + 1 < len(args) and args[index + 1].isdigit():
            rewritten.extend(['--interleave-round-count', args[index + 1]])
            index += 2
            continue
        if arg.startswith('--interleave-rounds='):
            value = arg.split('=', 1)[1]
            if value.isdigit():
                rewritten.extend(['--interleave-round-count', value])
                index += 1
                continue
        rewritten.append(arg)
        index += 1
    return rewritten


def _apply_interleave_rounds(interleave_rounds, attribute, quick, aa_band):
    """
    Map ``--interleave-rounds N`` onto the existing rounds machinery.

    An integer enables interleaving and sets the ``rounds`` attribute to
    N, which ``asv run`` already uses to walk commits each round.
    Returns ``(interleave_rounds, attribute, report_ci)``.
    """
    report_ci = False
    # bool is a subclass of int; the bare flag is True/False, not a count.
    if isinstance(interleave_rounds, int) and not isinstance(interleave_rounds, bool):
        if interleave_rounds < 1:
            raise util.UserError("--interleave-rounds N must be a positive integer")
        if quick:
            raise util.UserError("--interleave-rounds N cannot be combined with --quick")
        attribute = dict(attribute or {})
        attribute['rounds'] = interleave_rounds
        interleave_rounds = True
        report_ci = True
    if aa_band:
        report_ci = True
        if interleave_rounds is None:
            interleave_rounds = True
    return interleave_rounds, attribute, report_ci


class Continuous(Command):
    @classmethod
    def setup_arguments(cls, subparsers):
        parser = subparsers.add_parser(
            "continuous",
            help="Compare two commits directly",
            description="""Run a side-by-side comparison of two commits for
            continuous integration.""",
        )

        parser.add_argument(
            'base',
            nargs='?',
            default=None,
            help="""The commit/branch to compare against. By default, the
            parent of the tested commit.""",
        )
        parser.add_argument(
            'branch',
            default=None,
            help="""The commit/branch to test. By default, the first configured branch.""",
        )
        common_args.add_record_samples(parser, record_default=True)
        parser.add_argument(
            "--quick",
            "-q",
            action="store_true",
            help="""Do a "quick" run, where each benchmark function is
            run only once.  This is useful to find basic errors in the
            benchmark functions faster.  The results are unlikely to
            be useful, and thus are not saved.""",
        )
        parser.add_argument(
            "--interleave-rounds",
            action="store_true",
            default=None,
            help="""Interleave the base and head commits across rounds,
            flipping the order each round, in one process on one machine.
            --interleave-rounds N runs N rounds (the rounds attribute)
            and reports each ratio with a bootstrap 95%% CI.""",
        )
        parser.add_argument(
            "--interleave-round-count",
            type=int,
            default=None,
            help=argparse.SUPPRESS,
        )
        parser.add_argument(
            "--no-interleave-rounds", action="store_false", dest="interleave_rounds"
        )
        parser.add_argument(
            "--aa-band",
            action="store_true",
            help="""Run a second copy of the base commit in the round
            rotation (base, base', head, rotated each round). Flag a
            change only when the ratio's 95%% CI lies entirely outside
            the band of base' against base.""",
        )
        parser.add_argument(
            "--aa-band-split",
            action="store_true",
            help="""Fallback A/A band that splits the base samples in
            half. Both halves share the same rounds, so the band misses
            round-to-round drift. Prefer --aa-band.""",
        )
        # Backward compatibility for '--(no-)interleave-rounds'
        parser.add_argument(
            "--interleave-processes",
            action="store_true",
            default=False,
            dest="interleave_rounds",
            help=argparse.SUPPRESS,
        )
        parser.add_argument(
            "--no-interleave-processes",
            action="store_false",
            dest="interleave_rounds",
            help=argparse.SUPPRESS,
        )
        common_args.add_compare(parser, sort_default='ratio', only_changed_default=True)
        common_args.add_show_stderr(parser)
        common_args.add_bench(parser)
        common_args.add_machine(parser)
        common_args.add_environment(parser)
        common_args.add_launch_method(parser)
        parser.set_defaults(func=cls.run_from_args)
        # Accept "--interleave-rounds N" without consuming a branch name.
        orig_parse = parser.parse_known_args

        def parse_known_args(args=None, namespace=None):
            if args is not None:
                args = _rewrite_interleave_rounds(list(args))
            return orig_parse(args, namespace)

        parser.parse_known_args = parse_known_args

        return parser

    @classmethod
    def run_from_conf_args(cls, conf, args, **kwargs):
        return cls.run(
            conf=conf,
            branch=args.branch,
            base=args.base,
            factor=args.factor,
            split=args.split,
            only_changed=args.only_changed,
            sort=args.sort,
            use_stats=args.use_stats,
            show_stderr=args.show_stderr,
            bench=args.bench,
            attribute=args.attribute,
            machine=args.machine,
            env_spec=args.env_spec,
            record_samples=args.record_samples,
            append_samples=args.append_samples,
            quick=args.quick,
            interleave_rounds=args.interleave_rounds,
            interleave_round_count=args.interleave_round_count,
            aa_band=args.aa_band,
            aa_band_split=args.aa_band_split,
            launch_method=args.launch_method,
            **kwargs,
        )

    @classmethod
    def run(
        cls,
        conf,
        branch=None,
        base=None,
        factor=None,
        split=False,
        only_changed=True,
        sort='ratio',
        use_stats=True,
        show_stderr=False,
        bench=None,
        attribute=None,
        machine=None,
        env_spec=None,
        record_samples=False,
        append_samples=False,
        quick=False,
        interleave_rounds=None,
        interleave_round_count=None,
        aa_band=False,
        aa_band_split=False,
        launch_method=None,
        _machine_file=None,
    ):
        if aa_band and aa_band_split:
            raise util.UserError("Pass only one of --aa-band and --aa-band-split")
        if interleave_round_count is not None:
            interleave_rounds = interleave_round_count
        interleave_rounds, attribute, report_ci = _apply_interleave_rounds(
            interleave_rounds, attribute, quick, aa_band or aa_band_split
        )

        repo = get_repo(conf)
        repo.pull()

        if branch is None:
            branch = conf.branches[0]

        try:
            head = repo.get_hash_from_name(branch)

            if base is None:
                parent = repo.get_hash_from_parent(head)
            else:
                parent = repo.get_hash_from_name(base)
        except NoSuchNameError as exc:
            raise util.UserError(f"Unknown commit {exc}")

        commit_hashes = [head, parent]
        # Real A/A: base, a second checkout of base, then head.
        # The repeated base is stored under a distinct environment suffix
        # so its samples do not merge into the base result file.
        commit_slots = None
        if aa_band:
            commit_slots = [
                (parent, ''),
                (parent, AA_ENV_SUFFIX),
                (head, ''),
            ]
        run_objs = {}

        run_status = Run.run(
            conf,
            range_spec=commit_hashes,
            bench=bench,
            attribute=attribute,
            show_stderr=show_stderr,
            machine=machine,
            env_spec=env_spec,
            record_samples=record_samples,
            append_samples=append_samples,
            quick=quick,
            interleave_rounds=interleave_rounds,
            launch_method=launch_method,
            commit_slots=commit_slots,
            _returns=run_objs,
            _machine_file=_machine_file,
        )
        # 1 means nothing ran. 2 means a benchmark failed; the other
        # benchmarks and rounds still have results, so the table is printed.
        if run_status == 1 or 'environments' not in run_objs:
            return run_status

        log.flush()

        def results_iter(commit_hash, env_suffix=''):
            for env in run_objs['environments']:
                machine_name = run_objs['machine_params']['machine']
                filename = results.get_filename(machine_name, commit_hash, env.name + env_suffix)
                filename = os.path.join(conf.results_dir, filename)
                try:
                    result = results.Results.load(filename, machine_name)
                except util.UserError as err:
                    log.warning(str(err))
                    continue

                for name, benchmark in run_objs['benchmarks'].items():
                    params = benchmark['params']
                    version = benchmark['version']

                    value = result.get_result_value(name, params)
                    stats = result.get_result_stats(name, params)
                    samples = result.get_result_samples(name, params)
                    yield name, params, value, stats, samples, version, machine_name, env.name

        commit_names = {
            parent: repo.get_name_from_hash(parent),
            head: repo.get_name_from_hash(head),
        }

        if interleave_rounds or aa_band or aa_band_split:
            stored_ci, stored_band = _store_interleaved_comparisons(
                conf,
                run_objs,
                parent,
                head,
                aa_band=aa_band,
                aa_split=aa_band_split,
            )
        else:
            stored_ci, stored_band = None, None

        status = Compare.print_table(
            conf,
            parent,
            head,
            resultset_1=results_iter(parent),
            resultset_2=results_iter(head),
            factor=factor,
            split=split,
            use_stats=use_stats,
            only_changed=only_changed,
            sort=sort,
            commit_names=commit_names,
            bootstrap_ci=report_ci,
            aa_band=aa_band,
            aa_split=aa_band_split,
            aa_resultset=results_iter(parent, AA_ENV_SUFFIX) if aa_band else None,
            stored_ci=stored_ci,
            stored_band=stored_band,
        )
        worsened, improved = status

        color_print("")
        if worsened:
            color_print("SOME BENCHMARKS HAVE CHANGED SIGNIFICANTLY.", 'red')
            color_print("PERFORMANCE DECREASED.", 'red')
        elif improved:
            color_print("SOME BENCHMARKS HAVE CHANGED SIGNIFICANTLY.", 'green')
            color_print("PERFORMANCE INCREASED.", 'green')
        else:
            color_print("BENCHMARKS NOT SIGNIFICANTLY CHANGED.", 'green')

        if run_status:
            return run_status
        return worsened


def _as_ci_list(ci):
    if ci is None:
        return None
    return [ci[0], ci[1], ci[2]]


def _store_interleaved_comparisons(conf, run_objs, parent, head, aa_band, aa_split):
    """
    Write the ratio CI and A/A band into the head result files.

    Returns ``(stored_ci, stored_band)`` keyed by ``(benchmark, machine/env)``
    for the comparison table. Per-round samples are already in the files.
    """
    stored_ci = {}
    stored_band = {}
    machine_name = run_objs['machine_params']['machine']
    benchmarks = run_objs['benchmarks']

    for env in run_objs['environments']:
        machine_env = f"{machine_name}/{env.name}"
        head_path = os.path.join(
            conf.results_dir, results.get_filename(machine_name, head, env.name)
        )
        base_path = os.path.join(
            conf.results_dir, results.get_filename(machine_name, parent, env.name)
        )
        if not os.path.isfile(head_path) or not os.path.isfile(base_path):
            continue
        try:
            head_result = results.Results.load(head_path, machine_name)
            base_result = results.Results.load(base_path, machine_name)
        except util.UserError as err:
            log.warning(str(err))
            continue
        aa_result = None
        if aa_band:
            aa_path = os.path.join(
                conf.results_dir,
                results.get_filename(machine_name, parent, env.name + AA_ENV_SUFFIX),
            )
            if os.path.isfile(aa_path):
                try:
                    aa_result = results.Results.load(aa_path, machine_name)
                except util.UserError as err:
                    log.warning(str(err))

        ratio_ci = {}
        aa_bands = {}
        for name, benchmark in benchmarks.items():
            if name not in head_result.get_all_result_keys():
                continue
            if name not in base_result.get_all_result_keys():
                continue
            params = benchmark['params']
            before = base_result.get_result_samples(name, params)
            after = head_result.get_result_samples(name, params)
            paired = None
            if aa_result is not None and name in aa_result.get_all_result_keys():
                paired = aa_result.get_result_samples(name, params)
            count = max(len(before), len(after))
            cis = []
            bands = []
            for index in range(count):
                before_samples = before[index] if index < len(before) else None
                after_samples = after[index] if index < len(after) else None
                paired_samples = (
                    paired[index] if paired is not None and index < len(paired) else None
                )
                ci = _stats.bootstrap_median_ratio_ci(before_samples, after_samples)
                if aa_band:
                    band = _stats.bootstrap_median_ratio_ci(before_samples, paired_samples, seed=2)
                elif aa_split:
                    band = _stats.split_half_aa_band(before_samples, seed=2)
                else:
                    band = None
                cis.append(_as_ci_list(ci))
                bands.append(_as_ci_list(band))
            ratio_ci[name] = cis
            if aa_band or aa_split:
                aa_bands[name] = bands
            for unrolled, ci_row, band_row in unroll_result(name, params, cis, bands):
                stored_ci[(unrolled, machine_env)] = ci_row
                if aa_band or aa_split:
                    stored_band[(unrolled, machine_env)] = band_row

        if ratio_ci:
            head_result.set_comparison(parent, ratio_ci, aa_bands or None)
            head_result.save(conf.results_dir)

    return stored_ci, stored_band
