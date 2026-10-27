#!/usr/bin/env python3
"""Thin root dispatcher for the packaged Portkey key updater commands."""

from __future__ import annotations

import argparse
import importlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from portkey_key_updater import __app_name__, __version__  # noqa: E402

COMMANDS = {
    "check": ("portkey_key_updater.check_key", "Validate the current API key"),
    "renew": ("portkey_key_updater.renew_key", "Generate or renew the API key"),
    "bearer": ("portkey_key_updater.get_bearer", "Extract the browser bearer token"),
    "analyse": ("portkey_key_updater.analyse_env", "Analyse local environment key usage"),
    "sync": ("portkey_key_updater.update_secretmgr", "Synchronize secrets and keychain state"),
    "report": ("portkey_key_updater.report", "Generate the security report"),
}

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"


def color_enabled() -> bool:
    """Return whether ANSI color should be used for help output."""
    return sys.stdout.isatty() and os.getenv("NO_COLOR") is None and os.getenv("TERM") != "dumb"


def style(text: str, *codes: str) -> str:
    """Wrap text in ANSI codes when color output is enabled."""
    if not color_enabled():
        return text
    return f"{''.join(codes)}{text}{RESET}"


def colorize_help(text: str) -> str:
    """Apply light ANSI styling to already-formatted help text."""
    if not color_enabled():
        return text

    text = re.sub(r"^usage:", style("usage:", BOLD, YELLOW), text, count=1, flags=re.MULTILINE)

    for heading in ("positional arguments:", "options:", "Examples:"):
        text = text.replace(heading, style(heading, BOLD, CYAN))

    for name in COMMANDS:
        text = re.sub(rf"^(\s*)({re.escape(name)})(\s+)", rf"\1{style(name, BOLD, GREEN)}\3", text, flags=re.MULTILINE)

    text = re.sub(r"^(\s*)(-h, --help)(\s+)", rf"\1{style('-h, --help', GREEN)}\3", text, flags=re.MULTILINE)
    text = re.sub(r"^(\s*)(python3 main\.py .+)$", lambda m: m.group(1) + style(m.group(2), YELLOW), text, flags=re.MULTILINE)
    return text


class StyledArgumentParser(argparse.ArgumentParser):
    """Argument parser that colorizes help after layout is computed."""

    def format_help(self) -> str:
        return colorize_help(super().format_help())


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser for the root dispatcher."""
    parser = StyledArgumentParser(
        description="Portkey Key Updater root dispatcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 main.py check
  python3 main.py renew
  python3 main.py report
        """,
    )
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="command",
        required=True,
        help="Available tools",
        parser_class=StyledArgumentParser,
    )

    for name, (_, description) in COMMANDS.items():
        subparser = subparsers.add_parser(
            name,
            help=description,
            description=description,
        )
        subparser.add_argument(
            "command_args",
            nargs=argparse.REMAINDER,
            metavar="args",
            help="Arguments forwarded to the selected tool",
        )
    return parser


def dispatch(command: str, command_args: list[str]) -> int:
    """Dispatch a subcommand into the packaged module entrypoint."""
    module_name, _ = COMMANDS[command]
    module = importlib.import_module(module_name)
    target = getattr(module, "main")

    original_argv = sys.argv[:]
    sys.argv = [f"{Path(original_argv[0]).name} {command}", *command_args]
    try:
        result = target()
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    finally:
        sys.argv = original_argv

    return 0 if result is None else int(result)


def main() -> int:
    """Entry point for the root dispatcher."""
    parser = build_parser()
    args = parser.parse_args()
    return dispatch(args.command, args.command_args)


if __name__ == "__main__":
    raise SystemExit(main())
