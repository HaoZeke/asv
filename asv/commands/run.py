# Licensed under a 3-clause BSD style license - see LICENSE.rst
import argparse
import logging
import os
import sys
import textwrap
import time
import traceback
from collections import defaultdict

from .. import environment, util
from ..benchmarks import Benchmarks
from ..console import log
from ..machine import Machine
from ..repo import NoSuchNameError, get_repo
from ..results import Results, get_existing_hashes, iter_results_for_machine_and_hash
from ..runner import run_benchmarks, skip_benchmarks
from . import Command, common_args
from .setup import Setup
from .show import Show

# Environment-name suffix for the repeated base commit used by --aa-band.
AA_ENV_SUFFIX = '~aa'


def interleaved_slot_order(slots, display_round):
    """
    Rotate *slots* left by one place per round.

    Round 1 keeps the given order, for example ``(base, base', head)``.
    Round 2 starts at the next slot, and so on.
    """
    if not slots:
        return []
    shift = (display_round - 1) % len(slots)
    return slots[shift:] + slots[:shift]


def unique_slot_commits(slots):
    """Commit hashes in *slots*, in order, without repeats."""
    commits = []
    for commit_hash, _suffix in slots:
        if commit_hash and commit_hash not in commits:
            commits.append(commit_hash)
    return commits


def install_commits_once(env, conf, repo, commits):
    """
    Install each commit in *commits* once.

    Returns ``(retained, durations)``. ``retained`` maps a commit hash
    to the environment that has it installed, or ``None`` when that
    install failed. A repeated hash does not install again.
    """
    retained = {}
    durations = {}
    for commit_hash in commits:
        if not commit_hash or commit_hash in retained:
            continue
        started = time.time()
        try:
            retained[commit_hash] = env.retain_commit(conf, repo, commit_hash)
        except util.ProcessError:
            retained[commit_hash] = None
        durations[commit_hash] = time.time() - started
    return retained, durations


def runner_for_interleaved_slot(env, commit_hash, retained):
    """
    Environment that should run this interleaved slot.

    When *retained* already has *commit_hash*, that install is reused
    and the caller must not build or install again. ``None`` means the
    retained install failed.
    """
    if retained is not None and commit_hash in retained:
        return retained[commit_hash]
    return env


def _do_build(args):
    env, conf, repo, commit_hash = args
    started_at = time.time()
    success = False
    try:
        with log.set_level(logging.WARNING):
            env.install_project(conf, repo, commit_hash)
        success = True
    except util.ProcessError:
        pass
    duration = time.time() - started_at
    return (env.name, (success, duration))


def _do_build_multiprocess(args_sets):
    """
    multiprocessing callback to build the project in one particular
    environment.
    """
    try:
        res = []
        for args in args_sets:
            res.append(_do_build(args))
        return res
    except BaseException as exc:
        raise util.ParallelFailure(str(exc), exc.__class__, traceback.format_exc())


class Run(Command):
    @classmethod
    def setup_arguments(cls, subparsers):
        parser = subparsers.add_parser(
            "run",
            help="Run a benchmark suite",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description=textwrap.dedent(
                """
                Run a benchmark suite.

                examples:
                  asv run main             run for one branch
                  asv run main^!           run for one commit (git)
                  asv run "--merges main"  run for only merge commits (git)
                """
            ),
        )

        cls._setup_arguments(parser)

        parser.set_defaults(func=cls.run_from_args)

        return parser

    @classmethod
    def _setup_arguments(cls, parser, env_default_same=False):
        parser.add_argument(
            'range',
            nargs='?',
            default=None,
            help="""Range of commits to benchmark.  For a git
            repository, this is passed as the first argument to ``git
            rev-list``; or Mercurial log command. See 'specifying ranges'
            section of the `gitrevisions` manpage, or 'hg help revisions',
            for more info. Also accepts the
            special values 'NEW', 'ALL', 'EXISTING', 'TAGS', and
            'HASHFILE:xxx'.
            'NEW' will benchmark all commits since the latest
            benchmarked on this machine.
            'ALL' will benchmark all commits in the project.
            'EXISTING' will benchmark against all commits for which there
            are existing benchmarks on any machine.
            'TAGS' will benchmark against all tags in the project.
            'HASHFILE:xxx' will benchmark only a specific set of hashes
            given in the file named 'xxx' ('-' means stdin), which must
            have one hash per line.
            By default, will benchmark
            the head of each configured of the branches.""",
        )
        parser.add_argument(
            "--date-period",
            type=common_args.time_period,
            default=None,
            help="""Pick only one commit in each given time period.
            For example: 1d (daily), 1w (weekly), 1M (monthly), 1q (quarterly), 1y (yearly).""",
        )
        parser.add_argument(
            "--steps",
            "-s",
            type=common_args.positive_int,
            default=None,
            help="""Maximum number of steps to benchmark.  This is
            used to subsample the commits determined by range to a
            reasonable number.""",
        )
        common_args.add_bench(parser)
        parser.add_argument(
            "--profile",
            "-p",
            action="store_true",
            help="""In addition to timing, run the benchmarks through
            the `cProfile` profiler and store the results.""",
        )
        common_args.add_parallel(parser)
        common_args.add_show_stderr(parser)
        parser.add_argument(
            "--durations",
            action="store",
            metavar='N',
            nargs='?',
            type=common_args.positive_int_or_inf,
            default=0,
            const='all',
            help="Display total duration for N (or 'all') slowest benchmarks",
        )
        parser.add_argument(
            "--quick",
            "-q",
            action="store_true",
            help="""Do a "quick" run, where each benchmark function is
            run only once.  This is useful to find basic errors in the
            benchmark functions faster.  The results are unlikely to
            be useful, and thus are not saved.""",
        )
        common_args.add_environment(parser, default_same=env_default_same)
        parser.add_argument(
            "--set-commit-hash",
            default=None,
            help="""Set the commit hash to use when recording benchmark
            results. This makes results to be saved also when using an
            existing environment.""",
        )
        common_args.add_launch_method(parser)
        parser.add_argument(
            "--dry-run",
            "-n",
            action="store_true",
            default=None,
            help="""Do not save any results to disk.""",
        )
        common_args.add_machine(parser)
        parser.add_argument(
            "--skip-existing-successful",
            action="store_true",
            help="""Skip running benchmarks that have previous successful
            results""",
        )
        parser.add_argument(
            "--skip-existing-failed",
            action="store_true",
            help="""Skip running benchmarks that have previous failed
            results""",
        )
        parser.add_argument(
            "--skip-existing-commits",
            action="store_true",
            help="""Skip running benchmarks for commits that have existing
            results""",
        )
        parser.add_argument(
            "--skip-existing",
            "-k",
            action="store_true",
            help="""Skip running benchmarks that have previous successful
            or failed results""",
        )
        common_args.add_record_samples(parser)
        parser.add_argument(
            "--interleave-rounds",
            action="store_true",
            default=False,
            help="""Interleave benchmarks with multiple rounds across
            commits. This can avoid measurement biases from commit ordering,
            can take longer.""",
        )
        parser.add_argument(
            "--no-interleave-rounds", action="store_false", dest="interleave_rounds"
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
        parser.add_argument("--no-pull", action="store_true", help="Do not pull the repository")

    @classmethod
    def run_from_conf_args(cls, conf, args, **kwargs):
        return cls.run(
            conf=conf,
            range_spec=args.range,
            steps=args.steps,
            date_period=args.date_period,
            bench=args.bench,
            attribute=args.attribute,
            parallel=args.parallel,
            show_stderr=args.show_stderr,
            quick=args.quick,
            profile=args.profile,
            env_spec=args.env_spec,
            set_commit_hash=args.set_commit_hash,
            dry_run=args.dry_run,
            machine=args.machine,
            skip_successful=args.skip_existing_successful or args.skip_existing,
            skip_failed=args.skip_existing_failed or args.skip_existing,
            skip_existing_commits=args.skip_existing_commits,
            record_samples=args.record_samples,
            append_samples=args.append_samples,
            pull=not args.no_pull,
            interleave_rounds=args.interleave_rounds,
            launch_method=args.launch_method,
            durations=args.durations,
            **kwargs,
        )

    @classmethod
    def run(
        cls,
        conf,
        range_spec=None,
        steps=None,
        date_period=None,
        bench=None,
        attribute=None,
        parallel=1,
        show_stderr=False,
        quick=False,
        profile=False,
        env_spec=None,
        set_commit_hash=None,
        dry_run=False,
        machine=None,
        _machine_file=None,
        skip_successful=False,
        skip_failed=False,
        skip_existing_commits=False,
        record_samples=False,
        append_samples=False,
        pull=True,
        interleave_rounds=False,
        launch_method=None,
        durations=0,
        commit_slots=None,
        after_round=None,
        _returns={},  # noqa: B006
    ):
        machine_params = Machine.load(machine_name=machine, _path=_machine_file, interactive=True)
        machine_params.save(conf.results_dir)

        environments = list(environment.get_environments(conf, env_spec))

        has_existing_env = any(
            isinstance(env, environment.ExistingEnvironment) for env in environments
        )
        existing_only = environment.is_existing_only(environments)

        # Existing environments do not install from the repo. Use NoRepository
        # only when the user also does not pass a commit range / set-commit-hash
        # for labeling results. If they do, keep the real DVCS so hashes resolve.
        if existing_only and set_commit_hash is None and range_spec is None:
            conf.dvcs = "none"

        if interleave_rounds:
            if dry_run:
                raise util.UserError("--interleave-rounds and --dry-run cannot be used together")
            if has_existing_env:
                raise util.UserError(
                    "--interleave-rounds cannot be used with existing environment (or python=same)"
                )
        elif interleave_rounds is None:
            # Enable if possible
            interleave_rounds = not (dry_run or has_existing_env)

        if append_samples:
            record_samples = True

        repo = get_repo(conf)
        if pull:
            repo.pull()

        if set_commit_hash is not None:
            set_commit_hash = repo.get_hash_from_name(set_commit_hash)

        # Track failures across the run command
        failures = False

        # Comparison period for date_period filtering
        old_commit_hashes = None

        if range_spec is None:
            try:
                commit_hashes = list({repo.get_hash_from_name(branch) for branch in conf.branches})
            except NoSuchNameError as exc:
                raise util.UserError(f'Unknown branch {exc} in configuration')
        elif range_spec == "EXISTING":
            commit_hashes = get_existing_hashes(conf.results_dir)
        elif range_spec == "NEW":
            # New commits on each configured branches
            old_commit_hashes = get_existing_hashes(conf.results_dir)
            commit_hashes = repo.get_new_branch_commits(conf.branches, old_commit_hashes)
        elif range_spec == "TAGS":
            # All tags on each configured branches
            commit_hashes = list(reversed(list(repo.get_tags().keys())))
        elif range_spec == "ALL":
            # All commits on each configured branches
            commit_hashes = repo.get_new_branch_commits(conf.branches, [])
        elif range_spec == "HEAD":
            commit_hashes = [repo.get_hash_from_name("HEAD")]
        elif isinstance(range_spec, str) and range_spec.startswith('HASHFILE:'):
            hashfn = range_spec[9:]
            if hashfn == '-':
                hashstr = sys.stdin.read()
            elif os.path.isfile(hashfn):
                with open(hashfn, 'r') as f:
                    hashstr = f.read()
            else:
                log.error(f'Requested commit hash file "{hashfn}" is not a file')
                return 1
            commit_hashes = []
            for h in hashstr.split("\n"):
                h = h.strip()
                if h:
                    try:
                        commit_hashes.append(repo.get_hash_from_name(h))
                    except NoSuchNameError:
                        log.warning(f"Unknown commit hash {h} in input file")
        elif isinstance(range_spec, list):
            commit_hashes = range_spec
        else:
            commit_hashes = repo.get_hashes_from_range(range_spec)

        if date_period is not None:
            commit_hashes = repo.filter_date_period(commit_hashes, date_period, old_commit_hashes)

        if steps is not None:
            commit_hashes = util.pick_n(commit_hashes, steps)

        if len(commit_hashes) == 0:
            log.error("No commit hashes selected")
            return 1

        Setup.perform_setup(environments, parallel=parallel)
        if len(environments) == 0:
            log.error("No environments selected")
            return 1

        # Range / commit IDs with existing environments only label results
        # (no checkout/install). Previously this raised (issue #1464).
        if range_spec is not None and existing_only:
            log.info(
                "Using existing environment(s): commit range labels results "
                "only (project is not built or installed)"
            )

        benchmarks = Benchmarks.discover(conf, repo, environments, commit_hashes, regex=bench)
        benchmarks.save()
        if len(benchmarks) == 0:
            if bench == ["just-discover"]:
                return 0
            else:
                log.error("No benchmarks selected")
                return 1

        parallel, multiprocessing = util.get_multiprocessing(parallel)

        _returns['benchmarks'] = benchmarks
        _returns['environments'] = environments
        _returns['machine_params'] = machine_params.__dict__

        if attribute and 'rounds' in attribute:
            max_rounds = int(attribute['rounds'])
        else:
            max_rounds = max(b.get('rounds', 1) for b in benchmarks.values())

        skipped_benchmarks = defaultdict(lambda: set())

        for commit_hash in commit_hashes:
            if skip_successful or skip_failed or skip_existing_commits:
                try:
                    for result in iter_results_for_machine_and_hash(
                        conf.results_dir, machine_params.machine, commit_hash
                    ):
                        if skip_existing_commits:
                            skipped_benchmarks[commit_hash] = True
                            break

                        for key in result.get_result_keys(benchmarks):
                            if key not in benchmarks:
                                continue

                            value = result.get_result_value(key, benchmarks[key]['params'])

                            failed = value is None or (isinstance(value, list) and None in value)

                            if skip_failed and failed:
                                skipped_benchmarks[(commit_hash, result.env_name)].add(key)
                            if skip_successful and not failed:
                                skipped_benchmarks[(commit_hash, result.env_name)].add(key)
                except OSError:
                    pass

        # --aa-band adds a second copy of the base commit as its own slot.
        # Each slot is (checkout hash, result env-name suffix).
        if commit_slots is None:
            schedule = [(commit_hash, '') for commit_hash in commit_hashes]
        else:
            schedule = list(commit_slots)

        # Count work that will actually run (respects -k / skip-existing*).
        steps = 0
        for commit_hash, _env_suffix in schedule:
            if commit_hash in skipped_benchmarks and skipped_benchmarks[commit_hash] is True:
                continue
            for env in environments:
                skip_list = skipped_benchmarks[(commit_hash, env.name)]
                for bench in benchmarks:
                    if bench not in skip_list:
                        steps += 1

        log.info(
            f"Running {steps} total benchmarks "
            f"({len(schedule)} commits * {len(environments)} "
            f"environments * {len(benchmarks)} benchmarks"
            + (
                "; after skip-existing filters"
                if (skip_successful or skip_failed or skip_existing_commits)
                else ""
            )
            + ")"
        )

        progress_rounds = max_rounds
        if interleave_rounds:
            run_round_set = [[j] for j in range(max_rounds, 0, -1)]
        else:
            run_round_set = [None]
            if quick:
                progress_rounds = 1
        log.set_nitems(steps * progress_rounds)

        if launch_method is None:
            # Allow the users to set the launch_method by the command line argument
            # if they didn't set it, use the one in the config
            # and then ultimately default to 'auto' if not set in the config
            launch_method = conf.launch_method or 'auto'

        def iter_rounds_commits():
            for run_rounds in run_round_set:
                if commit_slots is not None and interleave_rounds:
                    # Rotate base, base', head (or any explicit slot list).
                    display_round = max_rounds - run_rounds[0] + 1
                    order = interleaved_slot_order(schedule, display_round)
                elif interleave_rounds and run_rounds[0] % 2 == 0:
                    order = list(reversed(schedule))
                else:
                    order = schedule
                for slot in order:
                    yield run_rounds, slot

        build_durations = defaultdict(lambda: 0)
        retained_by_env = {}
        if interleave_rounds:
            commits = unique_slot_commits(schedule)
            for env in environments:
                if not env.can_install_project():
                    continue
                retained, retain_durations = install_commits_once(env, conf, repo, commits)
                retained_by_env[env.name] = retained
                for commit_hash, duration in retain_durations.items():
                    build_durations[(commit_hash, env.name)] += duration
        slots_done = 0

        def finish_slot(run_rounds):
            nonlocal slots_done
            if after_round is None or not interleave_rounds or not run_rounds:
                return
            slots_done += 1
            if slots_done >= len(schedule):
                display_round = max_rounds - run_rounds[0] + 1
                after_round(display_round, skipped_benchmarks)
                slots_done = 0

        for run_rounds, slot in iter_rounds_commits():
            commit_hash, env_suffix = slot
            if commit_hash in skipped_benchmarks and skipped_benchmarks[commit_hash] is True:
                finish_slot(run_rounds)
                continue

            active_environments = [
                env
                for env in environments
                if set(benchmarks.keys()).difference(skipped_benchmarks[(commit_hash, env.name)])
            ]

            if not active_environments:
                finish_slot(run_rounds)
                continue

            if commit_hash:
                if interleave_rounds:
                    round_info = f" (round {max_rounds - run_rounds[0] + 1}/{max_rounds})"
                else:
                    round_info = ""

                commit_name = repo.get_decorated_hash(commit_hash, 8)
                if env_suffix == AA_ENV_SUFFIX:
                    commit_name = f"{commit_name} (base')"
                log.info(f"For {conf.project} commit {commit_name}{round_info}:")

            with log.indent():
                for subenv in util.iter_chunks(active_environments, parallel):
                    successes = {}
                    env_to_install = []
                    slot_runner = {}
                    for env in subenv:
                        if interleave_rounds and env.name in retained_by_env:
                            runner = runner_for_interleaved_slot(
                                env, commit_hash, retained_by_env[env.name]
                            )
                            slot_runner[env.name] = runner
                            successes[env.name] = (runner is not None, 0)
                            continue
                        slot_runner[env.name] = env
                        if not env.can_install_project():
                            successes[env.name] = (True, 0)
                            continue
                        ready = env.installed_commit_hash == commit_hash
                        successes[env.name] = (ready, 0)
                        if not ready:
                            env_to_install.append(env)

                    subenv_name = ', '.join([x.name for x in env_to_install])

                    if subenv_name:
                        log.info(f"Building for {subenv_name}")

                    with log.indent():
                        args = [(env, conf, repo, commit_hash) for env in env_to_install]

                        if parallel != 1:
                            # Parallel run only for environments with different dir_names
                            args_sets = defaultdict(list)
                            for arg in args:
                                args_sets[arg[0].dir_name].append(arg)
                            args_sets = args_sets.values()

                            try:
                                with util.get_multiprocessing_pool(parallel) as pool:
                                    res = []
                                    for r in pool.map(_do_build_multiprocess, args_sets):
                                        res.extend(r)
                                    successes.update(dict(res))
                            except util.ParallelFailure as exc:
                                exc.reraise()
                        else:
                            successes.update(dict(map(_do_build, args)))

                    for env in subenv:
                        success, duration = successes[env.name]

                        build_duration_key = (commit_hash, env.name)
                        build_durations[build_duration_key] += duration
                        build_duration = build_durations[build_duration_key]

                        params = dict(machine_params.__dict__)
                        params['python'] = env.python
                        params.update(env.requirements)

                        skip_save = dry_run or (
                            isinstance(env, environment.ExistingEnvironment)
                            and set_commit_hash is None
                            and commit_hash is None
                        )

                        skip_list = skipped_benchmarks[(commit_hash, env.name)]
                        benchmark_set = benchmarks.filter_out(skip_list)

                        if set_commit_hash is not None:
                            commit_hash = set_commit_hash

                        result = Results(
                            params,
                            env.requirements,
                            commit_hash,
                            repo.get_date(commit_hash),
                            env.python,
                            env.name + env_suffix,
                            env.env_vars,
                        )

                        if not skip_save:
                            result.load_data(conf.results_dir)

                        if build_duration != 0:
                            result.set_build_duration(build_duration)

                        # If we are interleaving commits, we need to
                        # append samples (except for the first round)
                        # and record samples (except for the final
                        # round).
                        force_append_samples = interleave_rounds and run_rounds[0] < max_rounds
                        force_record_samples = interleave_rounds and run_rounds[0] > 1

                        if success:
                            display_round = None
                            if interleave_rounds and run_rounds:
                                display_round = max_rounds - run_rounds[0] + 1
                            run_benchmarks(
                                benchmark_set,
                                slot_runner.get(env.name) or env,
                                results=result,
                                show_stderr=show_stderr,
                                quick=quick,
                                profile=profile,
                                extra_params=attribute,
                                record_samples=(record_samples or force_record_samples),
                                append_samples=(append_samples or force_append_samples),
                                run_rounds=run_rounds,
                                launch_method=launch_method,
                                round_number=display_round,
                            )
                        else:
                            skip_benchmarks(benchmark_set, env, results=result)

                        if not skip_save:
                            result.save(conf.results_dir)

                        failures = failures or any(code != 0 for code in result.errcode.values())

                        if durations > 0:
                            duration_set = Show._get_durations([(machine, result)], benchmark_set)
                            log.info(
                                cls.format_durations(duration_set[(machine, env.name)], durations)
                            )

            finish_slot(run_rounds)

        if failures:
            return 2

    @classmethod
    def format_durations(cls, durations, num_durations):
        items = list(durations.items())
        items.sort(key=lambda x: (-x[1], x[0]))

        rows = [["benchmark", "total duration"]]
        total = 0

        for j, (name, duration) in enumerate(items):
            if j >= num_durations:
                rows.append(["...", "..."])
                break
            rows.append([name, util.human_time(duration)])

        total = sum(durations.values())
        rows.append(["total", util.human_time(total)])

        msg = util.format_text_table(rows, num_headers=1)
        return msg
