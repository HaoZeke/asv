# Licensed under a 3-clause BSD style license - see LICENSE.rst

from . import Command
from .. import machine


class Machine(Command):
    @classmethod
    def setup_arguments(cls, subparsers):
        parser = subparsers.add_parser(
            "machine", help="Define information about this machine",
            description="""
            Defines information about this machine.  If no arguments
            are provided, an interactive console session will be used
            to ask questions about the machine.
            """)

        defaults = machine.Machine.get_defaults()
        for name, description in machine.Machine.fields:
            parser.add_argument(f'--{name}', default=defaults[name], help=description)

        parser.add_argument('--yes', default=False, action='store_true',
                            help="Accept all questions")

        parser.set_defaults(func=cls.run_from_args)

        return parser

    @classmethod
    def run_from_conf_args(cls, conf, args):
        return cls.run(**vars(args))

    @classmethod
    def run(cls, **kwargs):
        defaults = machine.Machine.get_defaults()
        different = {
            key: kwargs.get(key)
            for key, val in defaults.items()
            if kwargs.get(key) != val
        }
        use_defaults = kwargs['yes']

        machine.Machine.load(
            force_interactive=not different, use_defaults=use_defaults, **different
        )
