"""`config`: the one command that shows the configuration in force, writes a
key of it (`configwrite.py`) and does the machine's setup and hardening
(`setup.py`). Which of them runs is decided by what the command line carries:
a setup flag, a key and a value, or nothing."""

from .config import show_config
from .configwrite import write_key
from .setup import run_harden, run_setup


def cmd_config(args):
    if args.setup:
        run_setup(args)
    elif args.harden:
        run_harden()
    elif args.key:
        write_key(args)
    else:
        show_config(args)
