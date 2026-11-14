# Licensed under a 3-clause BSD style license - see LICENSE.rst

import argparse
import os

from asv_runner.console import color_print

from .. import results, util
from ..console import log
from ..repo import NoSuchNameError, get_repo
from . import Command, common_args
from .compare import Compare
from .run import Run


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
    N, which ``asv run`` already uses to flip commit order each round.
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
            help="""With interleaved rounds, compare the base commit with
            itself and flag a change only when the ratio's 95%% CI lies
            entirely outside that A/A band.""",
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
        launch_method=None,
        _machine_file=None,
    ):
        if interleave_round_count is not None:
            interleave_rounds = interleave_round_count
        interleave_rounds, attribute, report_ci = _apply_interleave_rounds(
            interleave_rounds, attribute, quick, aa_band
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
        run_objs = {}

        result = Run.run(
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
            _returns=run_objs,
            _machine_file=_machine_file,
        )
        if result:
            return result

        log.flush()

        def results_iter(commit_hash):
            for env in run_objs['environments']:
                machine_name = run_objs['machine_params']['machine']
                filename = results.get_filename(machine_name, commit_hash, env.name)
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

        return worsened
