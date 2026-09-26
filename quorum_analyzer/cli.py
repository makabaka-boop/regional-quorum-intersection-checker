"""Command-line entry point.

The analyzer reads one JSON instance from stdin (the usual Compose command
path), a file argument, or ``--json-string`` and prints one JSON result.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from .analyzer import InvalidInstance, analyze, parse_instance


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quorum-analyzer",
        description="Analyze weighted, datacenter-constrained read/write quorums",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="JSON input file; defaults to standard input",
    )
    parser.add_argument(
        "--json-string",
        metavar="JSON",
        help="analyze this JSON string instead of reading a stream",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=None,
        help="pretty-print output with this indentation width",
    )
    return parser


def _load_payload(arguments: argparse.Namespace) -> Any:
    if arguments.json_string is not None and arguments.path is not None:
        raise ValueError("path and --json-string cannot both be supplied")

    if arguments.json_string is not None:
        text = arguments.json_string
    elif arguments.path is not None:
        with open(arguments.path, "r", encoding="utf-8") as input_file:
            text = input_file.read()
    else:
        text = sys.stdin.read()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)

    try:
        payload = _load_payload(arguments)
        instance = parse_instance(payload)
    except (OSError, UnicodeError, InvalidInstance, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = analyze(instance)
    print(json.dumps(result, ensure_ascii=False, indent=arguments.indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
