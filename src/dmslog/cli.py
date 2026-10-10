"""Command-line interface: serve, ingest, info, export, templates, kb."""

from __future__ import annotations

import argparse
import dataclasses
import platform
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from . import __version__, messages
from .buildinfo import get_commit
from .paths import check_sqlite_fts5, resolve_data_dir

# command name -> (help text, milestone that implements it)
STUB_COMMANDS: dict[str, tuple[str, str]] = {
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


def _interval(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        seconds = -1.0
    if not 0.2 <= seconds <= 3600:
        raise argparse.ArgumentTypeError(messages.WATCH_INVALID_INTERVAL.format(value=value))
    return seconds


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
    serve.add_argument("--watch", metavar="PATH", default=None, help=messages.HELP_SERVE_WATCH)
    serve.add_argument("--watch-case", metavar="NAME", default=None, help=messages.HELP_SERVE_WATCH_CASE)
    serve.add_argument(
        "--watch-interval", type=_interval, default=2.0, metavar="S", help=messages.HELP_SERVE_WATCH_INTERVAL
    )
    serve.add_argument("--all-logs", action="store_true", help=messages.HELP_WATCH_ALL)

    ingest = sub.add_parser(
        "ingest", help=messages.HELP_INGEST, description=messages.HELP_INGEST, parents=[data_dir_parent]
    )
    ingest.add_argument("path", metavar="PATH", help=messages.HELP_INGEST_PATH)
    ingest.add_argument("--case", metavar="NAME", default=None, help=messages.HELP_INGEST_CASE)
    ingest.add_argument("--all-logs", action="store_true", help=messages.HELP_INGEST_ALL)

    watch = sub.add_parser(
        "watch", help=messages.HELP_WATCH, description=messages.HELP_WATCH, parents=[data_dir_parent]
    )
    watch.add_argument("path", metavar="PATH", help=messages.HELP_WATCH_PATH)
    watch.add_argument("--case", metavar="NAME", required=True, help=messages.HELP_WATCH_CASE)
    watch.add_argument(
        "--interval", type=_interval, default=2.0, metavar="S", help=messages.HELP_WATCH_INTERVAL
    )
    watch.add_argument("--all-logs", action="store_true", help=messages.HELP_WATCH_ALL)

    sub.add_parser("info", help=messages.HELP_INFO, description=messages.HELP_INFO, parents=[data_dir_parent])
    _add_export_parser(sub, data_dir_parent)

    for name, (help_text, _milestone) in STUB_COMMANDS.items():
        stub = sub.add_parser(name, help=help_text, description=help_text, parents=[data_dir_parent])
        stub.add_argument("args", nargs=argparse.REMAINDER, help=messages.HELP_STUB_ARGS)
    return parser


def _add_export_parser(sub: argparse._SubParsersAction, data_dir_parent: argparse.ArgumentParser) -> None:
    from .report.export import DEFAULT_MAX_BYTES

    export = sub.add_parser(
        "export",
        help=messages.HELP_EXPORT_CMD,
        description=messages.HELP_EXPORT_CMD,
        parents=[data_dir_parent],
    )
    export.add_argument("--case", required=True, metavar="NAME", help=messages.HELP_EXPORT_CASE)
    export.add_argument("--ids", metavar="ID[,ID...]", default=None, help=messages.HELP_EXPORT_IDS)
    export.add_argument("--event", metavar="ID[,ID...]", default=None, help=messages.HELP_EXPORT_EVENT)
    export.add_argument("--filter-json", metavar="JSON|@FILE", default=None, help=messages.HELP_EXPORT_FILTER)
    mode = export.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--anonymize", dest="anonymize", action="store_true", help=messages.HELP_EXPORT_ANONYMIZE
    )
    mode.add_argument(
        "--no-anonymize", dest="anonymize", action="store_false", help=messages.HELP_EXPORT_NO_ANONYMIZE
    )
    export.set_defaults(anonymize=None)
    export.add_argument("--include-payloads", action="store_true", help=messages.HELP_EXPORT_PAYLOADS)
    export.add_argument("--context", type=int, default=0, metavar="N", help=messages.HELP_EXPORT_CONTEXT)
    export.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        metavar="N",
        help=messages.HELP_EXPORT_MAX_BYTES.format(default=DEFAULT_MAX_BYTES),
    )
    export.add_argument("--out", metavar="FILE", default=None, help=messages.HELP_EXPORT_OUT)
    export.add_argument(
        "--redaction-mode",
        choices=("strict", "standard"),
        default=None,
        help=messages.HELP_EXPORT_REDACTION_MODE,
    )


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
    print(
        messages.INGEST_PAYLOADS.format(
            payloads=result.payloads, linked=result.payloads_linked, seconds=result.payload_seconds
        )
    )
    for warning in result.warnings:
        print(messages.INGEST_WARNING.format(message=warning))
    print(messages.INGEST_CASE_DB.format(path=db_path))
    return EXIT_FAILED if result.sources_failed else 0


def _id_list_option(raw: str | None, option: str) -> list[int] | None:
    if raw is None:
        return None
    try:
        values = [int(part) for part in raw.split(",")]
    except ValueError:
        values = []
    if not values or any(v < 1 for v in values):
        raise ValueError(messages.EXPORT_CLI_BAD_LIST.format(option=option))
    return values


def _filter_option(raw: str | None) -> dict | None:
    import json

    if raw is None:
        return None
    try:
        text = Path(raw[1:]).read_text(encoding="utf-8-sig") if raw.startswith("@") else raw
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as exc:
        raise ValueError(messages.EXPORT_CLI_BAD_FILTER.format(error=type(exc).__name__)) from None
    return value


def _part_path(out: Path, name_index: int, total: int) -> Path:
    if total == 1:
        return out
    return out.with_name(f"{out.stem}-part{name_index}of{total}{out.suffix}")


def cmd_export(args: argparse.Namespace) -> int:
    from .ingest.pipeline import case_db_path
    from .report import export, redact
    from .store.db import connect
    from .store.filters import QueryInputError
    from .store.query import QueryTimeout

    data_dir = resolve_data_dir(getattr(args, "data_dir", None))
    try:
        ids = _id_list_option(args.ids, "--ids")
        events = _id_list_option(args.event, "--event")
        flt = _filter_option(args.filter_json)
        if ids is None and events is None and flt is None:
            raise ValueError(messages.EXPORT_CLI_NEED_SELECTION)
        selection = export.parse_selection({"ids": ids, "events": events, "filter": flt})
        profile = redact.load_profile(data_dir) if args.anonymize else None
        if profile is not None and getattr(args, "redaction_mode", None):
            profile = dataclasses.replace(profile, mode=args.redaction_mode)
        options = export.parse_options(
            {
                "anonymize": args.anonymize,
                "include_payloads": args.include_payloads,
                "context": args.context,
                "max_bytes": args.max_bytes,
            },
            profile,
        )
        db_path = case_db_path(data_dir, args.case)
        if not db_path.is_file():
            raise ValueError(messages.EXPORT_CASE_NOT_FOUND_DB.format(case=args.case, data_dir=data_dir))
        conn = connect(db_path, readonly=True)
        try:
            result = export.build_report(conn, selection, options)
        finally:
            conn.close()
    except (ValueError, QueryInputError, QueryTimeout, OSError) as exc:
        message = exc.message if isinstance(exc, export.ExportError) else str(exc)
        if isinstance(exc, redact.ProfileError):
            message = messages.REDACT_BAD_PROFILE.format(
                problem=messages.REDACT_PROFILE_PROBLEMS.get(exc.problem, exc.problem), field=exc.field
            )
        print(messages.EXPORT_CLI_FAILED.format(error=message), file=sys.stderr)
        return EXIT_FAILED

    for warning in result.warnings:
        print(messages.EXPORT_CLI_WARNING.format(message=warning), file=sys.stderr)
    if args.out:
        out = Path(args.out).expanduser()
        total = len(result.parts)
        try:
            for index, part in enumerate(result.parts, 1):
                target = _part_path(out, index, total)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(part.text.encode("utf-8"))  # CRLF already inside the text
                print(messages.EXPORT_CLI_WROTE.format(path=target, size=len(part.text.encode("utf-8"))))
        except OSError as exc:
            print(messages.EXPORT_CLI_FAILED.format(error=exc), file=sys.stderr)
            return EXIT_FAILED
    else:
        for part in result.parts:
            sys.stdout.buffer.write(part.text.encode("utf-8"))
        sys.stdout.buffer.flush()
    if result.redactor is not None:
        try:
            saved = redact.write_value_map(data_dir, args.case, result.redactor)
        except OSError:
            saved = None
        if saved is not None:
            print(messages.EXPORT_CLI_MAP.format(path=saved), file=sys.stderr)
    return 0


def _stop_signals() -> list:
    """Signals that mean "stop": SIGTERM, and SIGBREAK on Windows (Ctrl+C is KeyboardInterrupt)."""
    import signal

    return [getattr(signal, name) for name in ("SIGTERM", "SIGBREAK") if hasattr(signal, name)]


def _install_stop_handler(handler):
    """Install ``handler`` for the stop signals; returns a function restoring the previous handlers."""
    import signal

    previous = []
    for sig in _stop_signals():
        try:
            previous.append((sig, signal.signal(sig, handler)))
        except (ValueError, OSError):  # not the main thread, or the platform refuses
            continue

    def restore() -> None:
        for sig, old in previous:
            try:
                signal.signal(sig, old)
            except (ValueError, OSError, TypeError):
                pass

    return restore


def cmd_watch(args: argparse.Namespace) -> int:
    import threading
    import time

    from .ingest.discover import SourceNotFoundError, UnsafeArchiveError
    from .ingest.ers import CollectionConflictError
    from .ingest.pipeline import SCOPE_ALL, SCOPE_DEFAULT, IngestOptions, case_db_path
    from .ingest.watch import CycleReport, Watcher
    from .store.db import create_case_db

    check = check_sqlite_fts5()
    if not check["fts5"]:
        print(
            messages.SQLITE_FTS5_MISSING.format(version=check["sqlite"], error=check["error"]),
            file=sys.stderr,
        )
        return EXIT_FAILED
    if not Path(args.path).is_dir():
        print(messages.WATCH_NOT_A_FOLDER.format(path=args.path), file=sys.stderr)
        return EXIT_FAILED
    data_dir = resolve_data_dir(getattr(args, "data_dir", None))
    try:
        db_path = case_db_path(data_dir, args.case)
    except ValueError as exc:
        print(messages.INGEST_INVALID_CASE.format(error=exc), file=sys.stderr)
        return EXIT_FAILED
    scope = SCOPE_ALL if args.all_logs else SCOPE_DEFAULT
    scope_text = messages.INGEST_SCOPE_ALL if args.all_logs else messages.INGEST_SCOPE_DEFAULT
    print(
        messages.WATCH_START.format(path=args.path, case=args.case, interval=args.interval, scope=scope_text),
        flush=True,
    )

    def on_report(report: CycleReport) -> None:
        print(format_cycle_line(report, time.strftime("%H:%M:%S")), flush=True)
        for warning in report.result.warnings if report.result else ():
            print(messages.WATCH_WARNING.format(message=warning), file=sys.stderr, flush=True)

    def on_error(exc: Exception, delay: float) -> None:
        print(messages.WATCH_ERROR.format(error=exc, delay=delay), file=sys.stderr, flush=True)

    conn = create_case_db(db_path)
    stop = threading.Event()
    # SIGTERM: ask the loop to stop; a running ingestion is abandoned at its next batch boundary.
    restore_signals = _install_stop_handler(lambda _signum, _frame: stop.set())
    try:
        watcher = Watcher(conn, args.path, IngestOptions(scope=scope, case_name=args.case))
        watcher.run(stop, args.interval, on_report=on_report, on_error=on_error)
    except KeyboardInterrupt:
        stop.set()
    except CollectionConflictError as exc:
        print(messages.WATCH_CONFLICT.format(case=args.case, error=exc), file=sys.stderr)
        return EXIT_CONFLICT
    except (SourceNotFoundError, UnsafeArchiveError, OSError) as exc:
        print(messages.INGEST_UNSAFE.format(error=exc), file=sys.stderr)
        return EXIT_FAILED
    finally:
        restore_signals()
        conn.close()
    print(messages.WATCH_STOPPED, flush=True)
    return 0


def format_cycle_line(report, clock: str) -> str:
    """The one line printed for a cycle that changed something."""
    result = report.result
    parts = []
    for detail in report.news:
        template = (
            messages.WATCH_SOURCE
            if detail.action in ("new", "append", "resume")
            else messages.WATCH_SOURCE_ACTION
        )
        parts.append(template.format(records=detail.records, member=detail.member, action=detail.action))
    derived = ""
    if result is not None and (result.events_derived or result.links_created or result.payloads):
        derived = messages.WATCH_DERIVED.format(
            events=result.events_derived, links=result.links_created, payloads=result.payloads
        )
    return messages.WATCH_CYCLE.format(time=clock, sources=", ".join(parts), derived=derived).rstrip()


def cmd_serve(args: argparse.Namespace) -> int:
    from .server.app import serve

    check = check_sqlite_fts5()
    if not check["fts5"]:
        print(
            messages.SQLITE_FTS5_MISSING.format(version=check["sqlite"], error=check["error"]),
            file=sys.stderr,
        )
        return 1
    watch = None
    if args.watch is not None:
        from .ingest.pipeline import SCOPE_ALL, SCOPE_DEFAULT, build_plan, default_case_name
        from .ingest.reader import ReadLimits

        if not Path(args.watch).is_dir():
            print(messages.WATCH_NOT_A_FOLDER.format(path=args.watch), file=sys.stderr)
            return EXIT_FAILED
        try:
            case = args.watch_case or default_case_name(build_plan(args.watch, ReadLimits()))
        except OSError as exc:
            print(messages.INGEST_NOT_FOUND.format(error=exc), file=sys.stderr)
            return EXIT_FAILED
        watch = {
            "path": Path(args.watch),
            "case": case,
            "interval": args.watch_interval,
            "scope": SCOPE_ALL if args.all_logs else SCOPE_DEFAULT,
        }
    return serve(
        port=args.port,
        open_browser=not args.no_browser,
        data_dir=getattr(args, "data_dir", None),
        watch=watch,
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
        "export": cmd_export,
        "watch": cmd_watch,
        **{name: _stub_handler(name, milestone) for name, (_h, milestone) in STUB_COMMANDS.items()},
    }
    if args.command is None:
        parser.print_help()
        return 0
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
