import argparse

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dmslog", description="Log analyzer and viewer for ADMS adapters (online interfaces)"
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
