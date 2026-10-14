"""Lanzador de la interfaz gráfica de Red.ly Desktop.

Uso::

    python gui.py
    python gui.py --debug
"""

from __future__ import annotations

import argparse
import sys

from app.gui import run


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gui.py",
        description="Interfaz gráfica (Tkinter) de Red.ly Desktop.",
    )
    parser.add_argument("--debug", action="store_true", help="Activa el modo debug.")
    parser.add_argument("--version", action="version", version="gui.py 0.2.0")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run(debug=args.debug)


if __name__ == "__main__":
    sys.exit(main())
