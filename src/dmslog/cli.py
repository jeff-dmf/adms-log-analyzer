"""Command-line interface: serve, ingest, info, export, templates, kb."""

from __future__ import annotations

import argparse
import platform
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from . import __version__, messages
from .buildinfo import get_commit
from .paths import check_sqlite_fts5, resolve_data_dir

# command name -> (help text, milestone that implements it)
STUB_COMMANDS: dict[str, tuple[str, str]] = {
    "export": (messages.HELP_EXPORT, "M7"),
    "templates": (messages.HELP_TEMPLATES, "M2"),
    "kb": (messages.HELP_KB, "M3"),
}

EXIT_NOT_IMPLEMENTED = 2
EXIT_FAILED = 1
EXIT_CONFLICT = 3


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

    serve = sub.add_parser(
        "serve", help=messages.HELP_SERVE, description=messages.HELP_SERVE, parents=[data_dir_parent]
    )
    serve.add_argument("--port", type=_port, default=0, metavar="PORT", help=messages.HELP_PORT)
    serve.add_argument("--no-browser", action="store_true", help=messages.HELP_NO_BROWSER)

    ingest = sub.add_parser(
        "ingest", help=messages.HELP_INGEST, description=messages.HELP_INGEST, parents=[data_dir_parent]
    )
    ingest.add_argument("path", metavar="PATH", help=messages.HELP_INGEST_PATH)
    ingest.add_argument("--case", metavar="NAME", default=None, help=messages.HELP_INGEST_CASE)
    ingest.add_argument("--all-logs", action="store_true", help=messages.HELP_INGEST_ALL)

    sub.add_parser("info", help=messages.HELP_INFO, description=messages.HELP_INFO, parents=[data_dir_parent])

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
    print(f"cases: {_describe_cases(values['data_dir'])}")
    return 0


def _describe_cases(data_dir: Path) -> str:
    """One line per case found under <data-dir>/cases, or "none"."""
    from .ingest.pipeline import CASE_DB_FILENAME
    from .store.db import connect

    lines = []
    for db in sorted((data_dir / "cases").glob(f"*/{CASE_DB_FILENAME}")):
        name = db.parent.name
        try:
            conn = connect(db, readonly=True)
            try:
                sources = conn.execute("SELECT count(*) FROM source WHERE status='ready'").fetchone()[0]
                entries = conn.execute("SELECT count(*) FROM entry").fetchone()[0]
            finally:
                conn.close()
            lines.append(messages.INFO_CASE_LINE.format(name=name, sources=sources, entries=entries, path=db))
        except Exception as exc:  # a damaged case must not break `info`
            lines.append(messages.INFO_CASE_UNREADABLE.format(name=name, error=exc))
    return "\n  ".join(lines) if lines else messages.INFO_CASES_NONE


def cmd_ingest(args: argparse.Namespace) -> int:
    from .ingest.discover import SourceNotFoundError, UnsafeArchiveError
    from .ingest.ers import CollectionConflictError
    from .ingest.pipeline import (
        SCOPE_ALL,
        SCOPE_DEFAULT,
        IngestOptions,
        ProgressEvent,
        build_plan,
        case_db_path,
        default_case_name,
        run_ingest,
    )
    from .ingest.reader import ReadLimits
    from .store.db import create_case_db

    check = check_sqlite_fts5()
    if not check["fts5"]:
        print(
            messages.SQLITE_FTS5_MISSING.format(version=check["sqlite"], error=check["error"]),
            file=sys.stderr,
        )
        return EXIT_FAILED
    data_dir = resolve_data_dir(getattr(args, "data_dir", None))
    limits = ReadLimits()
    try:
        plan = build_plan(args.path, limits)
        case = args.case if args.case is not None else default_case_name(plan)
        db_path = case_db_path(data_dir, case)
    except (SourceNotFoundError, OSError) as exc:
        print(messages.INGEST_NOT_FOUND.format(error=exc), file=sys.stderr)
        return EXIT_FAILED
    except UnsafeArchiveError as exc:
        print(messages.INGEST_UNSAFE.format(error=exc), file=sys.stderr)
        return EXIT_FAILED
    except ValueError as exc:
        print(messages.INGEST_INVALID_CASE.format(error=exc), file=sys.stderr)
        return EXIT_FAILED

    scope = SCOPE_ALL if args.all_logs else SCOPE_DEFAULT
    scope_text = messages.INGEST_SCOPE_ALL if args.all_logs else messages.INGEST_SCOPE_DEFAULT
    print(messages.INGEST_START.format(path=args.path, case=case, scope=scope_text))
    interactive = sys.stdout.isatty()

    def progress(event: ProgressEvent) -> None:
        if event.member is None:
            return
        if event.phase == "progress" and interactive and event.source_total:
            percent = min(100, event.source_bytes * 100 // event.source_total)
            text = messages.INGEST_PROGRESS.format(member=event.member, percent=percent)
            print("\r" + text, end="", flush=True)
        elif event.phase == "source_done" and event.action not in (None, "unchanged"):
            line = messages.INGEST_SOURCE_DONE.format(
                action=event.action,
                member=event.member,
                records=event.source_records,
                size_mb=event.source_bytes / 1e6,
            )
            print(("\r" + " " * 79 + "\r" if interactive else "") + line)

    conn = create_case_db(db_path)
    try:
        options = IngestOptions(scope=scope, case_name=case, limits=limits)
        result = run_ingest(conn, args.path, options, progress)
    except CollectionConflictError as exc:
        print(messages.INGEST_CONFLICT.format(case=case, error=exc), file=sys.stderr)
        return EXIT_CONFLICT
    except (SourceNotFoundError, UnsafeArchiveError, OSError) as exc:
        print(messages.INGEST_UNSAFE.format(error=exc), file=sys.stderr)
        return EXIT_FAILED
    finally:
        conn.close()

    for detail in result.details:
        if detail.action == "error":
            failed = messages.INGEST_SOURCE_FAILED.format(member=detail.member, error=detail.error)
            print(failed, file=sys.stderr)
    print(
        messages.INGEST_SUMMARY.format(
            selected=result.sources_selected,
            listed=result.sources_listed,
            indexed=result.sources_indexed,
            unchanged=result.sources_unchanged,
            skipped=result.sources_skipped,
            failed=result.sources_failed,
        )
    )
    size_mb = result.bytes / 1e6
    print(messages.INGEST_SUMMARY_DATA.format(records=result.records, lines=result.lines, size_mb=size_mb))
    print(messages.INGEST_SUMMARY_TIME.format(seconds=result.seconds, rate=result.mb_per_s))
    print(messages.INGEST_EVENTS.format(events=result.events_derived, seconds=result.derive_seconds))
    print(messages.INGEST_LINKS.format(links=result.links_created, seconds=result.correlate_seconds))
    for warning in result.warnings:
        print(messages.INGEST_WARNING.format(message=warning))
    print(messages.INGEST_CASE_DB.format(path=db_path))
    return EXIT_FAILED if result.sources_failed else 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server.app import serve

    check = check_sqlite_fts5()
    if not check["fts5"]:
        print(
            messages.SQLITE_FTS5_MISSING.format(version=check["sqlite"], error=check["error"]),
            file=sys.stderr,
        )
        return 1
    return serve(
        port=args.port,
        open_browser=not args.no_browser,
        data_dir=getattr(args, "data_dir", None),
    )


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
        "ingest": cmd_ingest,
        **{name: _stub_handler(name, milestone) for name, (_h, milestone) in STUB_COMMANDS.items()},
    }
    if args.command is None:
        parser.print_help()
        return 0
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
