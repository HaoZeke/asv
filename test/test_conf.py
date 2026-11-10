# Licensed under a 3-clause BSD style license - see LICENSE.rst

import os
from os.path import dirname, join
import sys

from asv import commands, config, environment, util
from asv.commands import Command


def test_config():
    conf = config.Config.load(join(dirname(__file__), 'asv.conf.json'))

    assert conf.project == 'astropy'
    assert conf.matrix == {
        "numpy": ["1.8"],
        "Cython": [],
        "jinja2": [],
    }
    assert conf.benchmark_dir == 'benchmark'
    assert conf.branches == [None]
    assert conf.install_timeout == 3142  # gh-973
    assert conf.default_benchmark_timeout == 32.5  # gh-973


def test_config_default_benchmark_timeout():
    # gh-973
    conf = config.Config()
    assert conf.default_benchmark_timeout == 60.0


def test_config_default_install_timeout():
    # gh-391
    conf = config.Config()
    assert conf.install_timeout == 600


def test_launch_method_config_and_docs():
    conf = config.Config.from_json(
        {
            "version": 1,
            "project": "asv",
            "repo": ".",
            "launch_method": "spawn",
        }
    )
    assert conf.launch_method == "spawn"

    docs = join(dirname(__file__), "..", "docs", "source", "asv.conf.json.rst")
    changes = join(dirname(__file__), "..", "CHANGES.rst")
    with open(docs, encoding="utf-8") as fh:
        text = fh.read()
    assert "``launch_method``" in text
    for choice in ("spawn", "forkserver", "auto"):
        assert choice in text
    assert "--launch-method" in text
    assert "Since asv 0.6.6" in text
    assert "except on macOS" in text
    assert "macOS and Windows" not in text
    with open(changes, encoding="utf-8") as fh:
        notes = fh.read()
    assert "The default launch method (``auto``) is now ``spawn``" in notes
    fragment = join(
        dirname(__file__), "..", "changelog.d", "launch_method_docs.misc.rst"
    )
    with open(fragment, encoding="utf-8") as fh:
        fragment_text = fh.read()
    assert "launch_method" in fragment_text
    assert "0.6.6" in fragment_text


class CustomCommand(Command):
    @classmethod
    def setup_arguments(cls, subparsers):
        parser = subparsers.add_parser("custom", help="Custom command", description="Juts a test.")

        parser.set_defaults(func=cls.run_from_args)

        return parser

    @classmethod
    def run_from_conf_args(cls, conf, args):
        pass


def test_load_plugin():
    os.chdir(dirname(__file__))

    parser, subparsers = commands.make_argparser()
    args = parser.parse_args(['custom'])

    assert hasattr(args, 'func')

    args.func(args)

    for env in util.iter_subclasses(environment.Environment):
        print(env.__name__)
        if env.__name__ == 'MyEnvironment':
            break
    else:
        assert False, "Custom plugin not loaded"

def test_cmd_line_config():
    # issue 1538
    util.check_call(
        [sys.executable, "-m", "asv", "machine", "--yes", "--config", "test/asv.conf.json"],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
    )
