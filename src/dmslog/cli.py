"""Command-line interface: serve, ingest, info, export, templates, kb."""

from __future__ import annotations

import argparse
import platform
import sys
from collections.abc import Callable, Sequence

from . import __version__, messages
from .buildinfo import get_commit
from .paths import check_sqlite_fts5, resolve_data_dir

# command name -> (help text, milestone that implements it)
STUB_COMMANDS: dict[str, tuple[str, str]] = {
    "ingest": (messages.HELP_INGEST, "M1"),
    "export": (messages.HELP_EXPORT, "M7"),
    "templates": (messages.HELP_TEMPLATES, "M2"),
    "kb": (messages.HELP_KB, "M3"),
}

EXIT_NOT_IMPLEMENTED = 2


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(messages.INVALID_PORT.format(value=value))
    return port


def build_parser() -> argparse.ArgumentParser:
    # --data-dir is accepted both before and after the subcommand.
    data_dir_parent = argparse.ArgumentParser(add_help=False)
    data_dir_parent.add_argument(
        "--data-dir", metavar="DIR", default=argparse.SUPPRESS, help=messages.HELP_DATA_DIR
    )

    parser = argparse.ArgumentParser(
        prog="dmslog", description=messages.CLI_DESCRIPTION, parents=[data_dir_parent]
    )
    parser.add_argument("--version", action="version", version=__version__, help=messages.HELP_VERSION)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    serve = sub.add_parser("serve", help=messages.HELP_SERVE, description=messages.HELP_SERVE,
                           parents=[data_dir_parent])
    serve.add_argument("--port", type=_port, default=0, metavar="PORT", help=messages.HELP_PORT)
    serve.add_argument("--no-browser", action="store_true", help=messages.HELP_NO_BROWSER)

    sub.add_parser("info", help=messages.HELP_INFO, description=messages.HELP_INFO,
                   parents=[data_dir_parent])

    for name, (help_text, _milestone) in STUB_COMMANDS.items():
        stub = sub.add_parser(name, help=help_text, description=help_text, parents=[data_dir_parent])
        stub.add_argument("args", nargs=argparse.REMAINDER, help=messages.HELP_STUB_ARGS)
    return parser


def cmd_info(args: argparse.Namespace) -> int:
    check = check_sqlite_fts5()
    values = {
        "version": __version__,
        "commit": get_commit(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "sqlite": check["sqlite"],
        "fts5": "yes" if check["fts5"] else "NO",
        "data_dir": resolve_data_dir(getattr(args, "data_dir", None)),
    }
    for label, template in messages.INFO_LINES:
        print(f"{label}: {template.format(**values)}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server.app import serve

    check = check_sqlite_fts5()
    if not check["fts5"]:
        print(
            messages.SQLITE_FTS5_MISSING.format(version=check["sqlite"], error=check["error"]),
            file=sys.stderr,
        )
        return 1
    return serve(port=args.port, open_browser=not args.no_browser)


def _stub_handler(name: str, milestone: str) -> Callable[[argparse.Namespace], int]:
    def handler(_args: argparse.Namespace) -> int:
        print(messages.NOT_IMPLEMENTED.format(command=name, milestone=milestone), file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED

    return handler


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "serve": cmd_serve,
        "info": cmd_info,
        **{name: _stub_handler(name, milestone) for name, (_h, milestone) in STUB_COMMANDS.items()},
    }
    if args.command is None:
        parser.print_help()
        return 0
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
