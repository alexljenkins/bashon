"""Bashon command line interface."""

from __future__ import annotations

import sys
from typing import Any, Sequence

from .core import (
    collection_to_spec,
    command_to_spec,
    emit_error,
    emit_spec,
    emit_success,
    invoke_command,
    load_collection,
    parse_command_arguments,
    parse_target,
    render_collection_help,
    render_root_help,
    root_spec,
)
from .errors import BashonError, CommandNotFoundError, ParseError
from .registry import AliasRegistry, BUILTIN_NAMES


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Bashon CLI."""

    args = list(sys.argv[1:] if argv is None else argv)
    human, help_requested, remainder = _split_global_flags(args)
    registry = AliasRegistry.load()
    try:
        if not remainder:
            if human and not help_requested:
                _print(render_root_help(registry.aliases), stream="stdout")
            else:
                _print(
                    emit_spec(root_spec(registry.aliases), human=human, header=render_root_help(registry.aliases)),
                    stream="stdout",
                )
            return 0

        root_command, *rest = remainder
        if help_requested:
            rest = ["--help", *rest]

        if root_command in BUILTIN_NAMES:
            return _handle_builtin(root_command, rest, human=human, registry=registry)

        target = registry.aliases.get(root_command)
        if target is None:
            raise CommandNotFoundError(f"Unknown Bashon command or alias '{root_command}'.")
        return _handle_target(target, rest, human=human, prog=f"bashon {root_command}")
    except BashonError as exc:
        _print(emit_error(exc, human=human), stream="stderr" if human else "stdout")
        return 1


def _split_global_flags(argv: list[str]) -> tuple[bool, bool, list[str]]:
    human = False
    help_requested = False
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--human":
            human = True
            index += 1
            continue
        if token == "--help":
            help_requested = True
            index += 1
            continue
        break
    return human, help_requested, argv[index:]


def _handle_builtin(command: str, argv: list[str], *, human: bool, registry: AliasRegistry) -> int:
    if command == "run":
        return _builtin_run(argv, human=human)
    if command == "spec":
        return _builtin_spec(argv, human=human)
    if command == "add":
        return _builtin_add(argv, human=human, registry=registry)
    if command == "list":
        return _builtin_list(argv, human=human, registry=registry)
    if command == "remove":
        return _builtin_remove(argv, human=human, registry=registry)
    raise CommandNotFoundError(f"Unknown Bashon built-in '{command}'.")


def _builtin_run(argv: list[str], *, human: bool) -> int:
    if not argv or argv[0] == "--help":
        spec = {
            "kind": "builtin",
            "name": "run",
            "summary": "Run a target directly.",
            "parameters": [
                {"name": "target", "kind": "positional", "required": True},
                {"name": "args", "kind": "remainder", "required": False},
            ],
        }
        _print(emit_spec(spec, human=human, header="Usage: bashon run TARGET [ARGS...]"), stream="stdout")
        return 0
    target, rest = _require_target("bashon run", argv, "Run a target directly.")
    return _handle_target(target, rest, human=human, prog=f"bashon run {target}")


def _builtin_spec(argv: list[str], *, human: bool) -> int:
    if not argv or argv[0] == "--help":
        spec = {
            "kind": "builtin",
            "name": "spec",
            "summary": "Print command schema for a target.",
            "parameters": [
                {"name": "target", "kind": "positional", "required": True},
                {"name": "command", "kind": "positional", "required": False},
            ],
        }
        _print(emit_spec(spec, human=human, header="Usage: bashon spec TARGET [COMMAND]"), stream="stdout")
        return 0
    target = argv[0]
    command_name = argv[1] if len(argv) > 1 else None
    collection = load_collection(parse_target(target))
    if command_name:
        if command_name not in collection.commands:
            raise CommandNotFoundError(f"'{command_name}' is not defined in '{target}'.")
        payload = command_to_spec(collection.commands[command_name])
    else:
        payload = collection_to_spec(collection)
    _print(emit_spec(payload, human=human, header=render_collection_help(collection, prog=f"bashon spec {target}")), stream="stdout")
    return 0


def _builtin_add(argv: list[str], *, human: bool, registry: AliasRegistry) -> int:
    if len(argv) < 2 or argv[0] == "--help":
        spec = {
            "kind": "builtin",
            "name": "add",
            "summary": "Register an alias for a target.",
            "parameters": [
                {"name": "alias", "kind": "positional", "required": True},
                {"name": "target", "kind": "positional", "required": True},
            ],
        }
        _print(emit_spec(spec, human=human, header="Usage: bashon add ALIAS TARGET"), stream="stdout")
        return 0
    alias, target = argv[0], argv[1]
    registry.add(alias, target)
    _print(emit_success({"alias": alias, "target": target, "status": "added"}, human=human), stream="stdout")
    return 0


def _builtin_list(argv: list[str], *, human: bool, registry: AliasRegistry) -> int:
    if argv and argv[0] == "--help":
        spec = {"kind": "builtin", "name": "list", "summary": "List registered aliases.", "parameters": []}
        _print(emit_spec(spec, human=human, header="Usage: bashon list"), stream="stdout")
        return 0
    payload: Any = [{"alias": alias, "target": target} for alias, target in sorted(registry.aliases.items())]
    if human:
        text = "\n".join(f"{item['alias']}: {item['target']}" for item in payload) if payload else "No aliases registered."
        _print(text, stream="stdout")
        return 0
    _print(emit_success(payload, human=False), stream="stdout")
    return 0


def _builtin_remove(argv: list[str], *, human: bool, registry: AliasRegistry) -> int:
    if not argv or argv[0] == "--help":
        spec = {
            "kind": "builtin",
            "name": "remove",
            "summary": "Remove a registered alias.",
            "parameters": [{"name": "alias", "kind": "positional", "required": True}],
        }
        _print(emit_spec(spec, human=human, header="Usage: bashon remove ALIAS"), stream="stdout")
        return 0
    alias = argv[0]
    registry.remove(alias)
    _print(emit_success({"alias": alias, "status": "removed"}, human=human), stream="stdout")
    return 0


def _require_target(prog: str, argv: list[str], summary: str) -> tuple[str, list[str]]:
    if not argv or argv[0] == "--help":
        raise ParseError(f"{summary}\nUsage: {prog} TARGET [ARGS...]")
    return argv[0], argv[1:]


def _handle_target(target: str, argv: list[str], *, human: bool, prog: str) -> int:
    collection = load_collection(parse_target(target))
    if len(collection.commands) == 1:
        command = next(iter(collection.commands.values()))
        values, parser, help_requested = parse_command_arguments(command, argv, prog=prog)
        if help_requested:
            _print(
                emit_spec(command_to_spec(command), human=human, header=_format_human_help(parser.format_help())),
                stream="stdout",
            )
            return 0
        result = invoke_command(command, values)
        _print(emit_success(result, human=human), stream="stdout")
        return 0

    if not argv or argv[0] == "--help":
        _print(emit_spec(collection_to_spec(collection), human=human, header=render_collection_help(collection, prog=prog)), stream="stdout")
        return 0
    command_name, *rest = argv
    if command_name not in collection.commands:
        raise CommandNotFoundError(f"'{command_name}' is not a command in '{target}'.")
    command = collection.commands[command_name]
    values, parser, help_requested = parse_command_arguments(command, rest, prog=f"{prog} {command_name}")
    if help_requested:
        _print(
            emit_spec(command_to_spec(command), human=human, header=_format_human_help(parser.format_help())),
            stream="stdout",
        )
        return 0
    result = invoke_command(command, values)
    _print(emit_success(result, human=human), stream="stdout")
    return 0


def _print(text: str, *, stream: str) -> None:
    output = sys.stderr if stream == "stderr" else sys.stdout
    output.write(text)
    if not text.endswith("\n"):
        output.write("\n")


def _format_human_help(text: str) -> str:
    if text.startswith("usage:"):
        return "Usage:" + text[len("usage:") :]
    return text


if __name__ == "__main__":
    raise SystemExit(main())
