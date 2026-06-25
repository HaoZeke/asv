# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""\
Usage: python -masv.benchmark COMMAND [...]

Manage a single benchmark and, when run from the commandline, report
its runtime to a file.

commands:

  timing [...]
      Run timing benchmark for given Python statement.

internal commands:

  discover BENCHMARK_DIR RESULT_FILE
      Discover benchmarks in a given directory and store result to a file.
  setup_cache BENCHMARK_DIR BENCHMARK_ID
      Run setup_cache for given benchmark.
  run BENCHMARK_DIR BENCHMARK_ID QUICK PROFILE_PATH RESULT_FILE
      Run a given benchmark, and store result in a file.
  run_server BENCHMARK_DIR SOCKET_FILENAME
      Run a Unix socket forkserver.
"""

import os
import sys

from asv_runner.check import _check
from asv_runner.discovery import _discover
from asv_runner.run import _run
from asv_runner.server import _run_server
from asv_runner.setup_cache import _setup_cache as _setup_cache_impl
from asv_runner.timing import _timing


def _setup_cache(args):
    """Run setup_cache, ensuring module-level setup() hooks run first (#1592).

    Older asv_runner releases omit setups inside ``do_setup_cache``. Patch the
    method on the discovered benchmark class for this process so pandas-style
    module ``setup(*args, **kwargs)`` seed hooks apply during cache builds.
    """
    try:
        from asv_runner.benchmarks._base import Benchmark
    except ImportError:
        return _setup_cache_impl(args)

    original = Benchmark.do_setup_cache

    def do_setup_cache_with_module_setup(self):
        if self._setup_cache is None:
            return None
        import inspect
        for setup in self._setups:
            try:
                sig = inspect.signature(setup)
                # Bind with no positional args; skip if required params remain.
                sig.bind()
            except TypeError:
                continue
            setup()
        return self._setup_cache()

    Benchmark.do_setup_cache = do_setup_cache_with_module_setup
    try:
        return _setup_cache_impl(args)
    finally:
        Benchmark.do_setup_cache = original


def _help(args):
    print(__doc__)


commands = {
    'discover': _discover,
    'setup_cache': _setup_cache,
    'run': _run,
    'run_server': _run_server,
    'check': _check,
    'timing': _timing,
    '-h': _help,
    '--help': _help,
}


def main():
    # Remove asv package directory from `sys.path`. This script file resides
    # there although it's not part of the package, so Python prepends it to
    # `sys.path` on start which can shadow other modules. On Python 3.11+ it is
    # possible to use `PYTHONSAFEPATH` to prevent this, but the script needs to
    # work for older versions of Python.
    if (
        not getattr(sys.flags, 'safe_path', False)  # Python 3.11+ only.
        and sys.path[0] == os.path.dirname(os.path.abspath(__file__))
    ):
        sys.path.pop(0)

    if len(sys.argv) < 2:
        _help([])
        sys.exit(1)

    mode = sys.argv[1]
    args = sys.argv[2:]

    env = os.environ.copy()
    # --- Modify sys.path for the current interpreter ---
    if 'ASV_PYTHONPATH' in env:
        new_paths = env['ASV_PYTHONPATH'].split(os.pathsep)
        for path in reversed(new_paths):  # Add to the front to prioritize
            if path not in sys.path:
                sys.path.insert(0, path)
        # Remove ASV_PYTHONPATH from env, as it's no longer needed after sys.path update
        env.pop('ASV_PYTHONPATH')
    else:
        # Clean up sys.path if PYTHONPATH was set but ASV_PYTHONPATH is not
        if 'PYTHONPATH' in env:
            old_paths = env['PYTHONPATH'].split(os.pathsep)
            for path in old_paths:
                if path in sys.path:
                    sys.path.remove(path)

            env.pop('PYTHONPATH')

    if mode in commands:
        commands[mode](args)
        sys.exit(0)
    else:
        sys.stderr.write(f"Unknown mode {mode}\n")
        sys.exit(1)


if __name__ == '__main__':
    main()
