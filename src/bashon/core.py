"""Core command discovery, parsing, and execution for Bashon."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import inspect
import json
import re
import sys
from dataclasses import MISSING, asdict, dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any, Callable, Mapping, Sequence, Union, get_args, get_origin, get_type_hints

from .errors import CommandNotFoundError, ParseError, SerializationError, UnsupportedCallableError

BASHON_META_ATTR = "__bashon_meta__"
_DOC_PARAM_RE = re.compile(r"^:param\s+([a-zA-Z_][\w]*)\s*:\s*(.+)$")
_DOC_GOOGLE_RE = re.compile(r"^([a-zA-Z_][\w]*)\s*(?:\([^)]+\))?\s*:\s*(.+)$")
_MISSING = object()


def _is_missing(value: Any) -> bool:
    return (
        value is MISSING
        or value is _MISSING
        or value is inspect._empty
        or value.__class__.__name__ == "PydanticUndefinedType"
    )


@dataclass(frozen=True)
class Param:
    """Optional parameter metadata for ``typing.Annotated``."""

    help: str | None = None
    aliases: tuple[str, ...] = ()
    hidden: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "aliases", tuple(self.aliases))


@dataclass(frozen=True)
class BashonMeta:
    """Metadata attached by the ``@bashon`` decorator."""

    name: str | None = None
    help: str | None = None
    hidden: bool = False


@dataclass(frozen=True)
class TargetRef:
    """A parsed Bashon target."""

    original: str
    source: str
    kind: str
    callable_path: str | None = None


@dataclass(frozen=True)
class DocInfo:
    """Docstring metadata for help rendering."""

    summary: str
    description: str
    params: dict[str, str]


@dataclass(frozen=True)
class StructuredFieldSpec:
    """Flattened field metadata for structured parameters."""

    path: tuple[str, ...]
    annotation: Any
    required: bool
    default: Any = _MISSING
    help: str | None = None

    @property
    def cli_suffix(self) -> str:
        return ".".join(self.path)


@dataclass(frozen=True)
class ParameterSpec:
    """Runtime metadata for a function parameter."""

    name: str
    cli_name: str
    annotation: Any
    required: bool
    default: Any = _MISSING
    help: str | None = None
    aliases: tuple[str, ...] = ()
    hidden: bool = False
    positional: bool = False
    is_boolean_flag: bool = False
    structured_fields: tuple[StructuredFieldSpec, ...] = ()

    @property
    def is_structured(self) -> bool:
        return bool(self.structured_fields)


@dataclass(frozen=True)
class CommandSpec:
    """A resolved Bashon command."""

    name: str
    qualname: str
    summary: str
    description: str
    signature: inspect.Signature
    parameters: tuple[ParameterSpec, ...]
    invoke_target: Any
    signature_target: Callable[..., Any]
    is_langchain_tool: bool
    source: str


@dataclass(frozen=True)
class CommandCollection:
    """A set of commands exposed by a module, file, or class target."""

    target: TargetRef
    source_display: str
    commands: dict[str, CommandSpec]


@dataclass(frozen=True)
class ResolvedObject:
    """An object resolved from a module target path."""

    raw: Any
    bound: Any
    owner_class: type[Any] | None
    qualname: str


class BashonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser variant that raises parse errors instead of exiting."""

    def error(self, message: str) -> None:
        raise ParseError(message)


def bashon(
    obj: Any | None = None,
    *,
    name: str | None = None,
    help: str | None = None,
    hidden: bool = False,
) -> Any:
    """Mark a callable or compatible tool object as a Bashon command."""

    meta = BashonMeta(name=name, help=help, hidden=hidden)

    def decorate(target: Any) -> Any:
        for candidate in _iter_related_objects(target):
            try:
                setattr(candidate, BASHON_META_ATTR, meta)
            except Exception:
                continue
        return target

    if obj is None:
        return decorate
    return decorate(obj)


def parse_target(value: str) -> TargetRef:
    """Parse a Bashon target string."""

    source = value
    callable_path = None
    if ":" in value:
        left, _, right = value.rpartition(":")
        if left and right:
            source = left
            callable_path = right
    kind = "file" if source.endswith(".py") or Path(source).expanduser().is_file() else "module"
    return TargetRef(original=value, source=source, kind=kind, callable_path=callable_path)


def load_collection(target: TargetRef) -> CommandCollection:
    """Load and discover commands for a target."""

    module = _load_module(target)
    if target.callable_path:
        resolved = _resolve_path(module, target.callable_path)
        if inspect.isclass(resolved.bound):
            commands = _discover_class_commands(
                resolved.bound,
                f"{target.source}:{target.callable_path}",
                skip_unsupported=True,
            )
        else:
            commands = {
                _build_command_name(resolved, target.callable_path): _build_command(
                    resolved=resolved,
                    default_name=_build_command_name(resolved, target.callable_path),
                    source_display=f"{target.source}:{target.callable_path}",
                )
            }
    else:
        commands = _discover_module_commands(module, target.source)
    if not commands:
        raise CommandNotFoundError(f"No @bashon commands found in '{target.original}'.")
    return CommandCollection(target=target, source_display=target.original, commands=commands)


def parse_command_arguments(
    command: CommandSpec,
    argv: Sequence[str],
    *,
    prog: str,
) -> tuple[dict[str, Any], BashonArgumentParser, bool]:
    """Parse argv for a resolved command."""

    parser = BashonArgumentParser(
        prog=prog,
        add_help=False,
        description=command.description or command.summary or None,
    )
    parser.add_argument("--help", action="store_true", dest="_bashon_help", default=False, help="Show help.")

    for parameter in command.parameters:
        if parameter.is_structured:
            option_strings = [f"--{parameter.cli_name}", *parameter.aliases]
            parser.add_argument(
                *option_strings,
                dest=f"{parameter.name}__json",
                default=None,
                help=argparse.SUPPRESS if parameter.hidden else (parameter.help or "JSON object input."),
            )
            for field_spec in parameter.structured_fields:
                parser.add_argument(
                    f"--{parameter.cli_name}.{field_spec.cli_suffix}",
                    dest=f"{parameter.name}__{'__'.join(field_spec.path)}",
                    default=None,
                    help=argparse.SUPPRESS if parameter.hidden else (field_spec.help or _type_label(field_spec.annotation)),
                )
            continue

        if parameter.positional:
            parser.add_argument(
                parameter.name,
                help=argparse.SUPPRESS if parameter.hidden else (parameter.help or _type_label(parameter.annotation)),
            )
            continue

        option_strings = [f"--{parameter.cli_name}", *parameter.aliases]
        if parameter.is_boolean_flag:
            parser.add_argument(
                *option_strings,
                dest=parameter.name,
                default=bool(parameter.default),
                action=argparse.BooleanOptionalAction,
                help=argparse.SUPPRESS if parameter.hidden else parameter.help,
            )
            continue
        parser.add_argument(
            *option_strings,
            dest=parameter.name,
            required=parameter.required,
            default=None if _is_missing(parameter.default) else parameter.default,
            help=argparse.SUPPRESS if parameter.hidden else parameter.help,
        )

    if "--help" in argv:
        return {}, parser, True

    namespace = parser.parse_args(list(argv))
    if getattr(namespace, "_bashon_help"):
        return {}, parser, True

    values: dict[str, Any] = {}
    for parameter in command.parameters:
        if parameter.is_structured:
            values[parameter.name] = _build_structured_argument(parameter, namespace)
            continue
        raw_value = getattr(namespace, parameter.name)
        if raw_value is None and _is_missing(parameter.default):
            raise ParseError(f"Missing value for '{parameter.name}'.")
        if raw_value is None and not _is_missing(parameter.default):
            values[parameter.name] = parameter.default
            continue
        values[parameter.name] = _coerce_value(parameter.annotation, raw_value)
    return values, parser, False


def invoke_command(command: CommandSpec, values: Mapping[str, Any]) -> Any:
    """Invoke a resolved command."""

    if command.is_langchain_tool and callable(getattr(command.invoke_target, "invoke", None)):
        return command.invoke_target.invoke(dict(values))

    positional_args: list[Any] = []
    keyword_args: dict[str, Any] = {}
    for name, parameter in command.signature.parameters.items():
        if name not in values:
            continue
        value = values[name]
        if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
            positional_args.append(value)
        else:
            keyword_args[name] = value
    return command.invoke_target(*positional_args, **keyword_args)


def command_to_spec(command: CommandSpec) -> dict[str, Any]:
    """Return a machine-readable spec for a command."""

    return {
        "kind": "command",
        "name": command.name,
        "callable": command.qualname,
        "summary": command.summary,
        "description": command.description,
        "source": command.source,
        "parameters": [_parameter_to_spec(parameter) for parameter in command.parameters if not parameter.hidden],
    }


def collection_to_spec(collection: CommandCollection) -> dict[str, Any]:
    """Return a machine-readable spec for a collection."""

    return {
        "kind": "collection",
        "target": collection.target.original,
        "commands": [command_to_spec(command) for command in collection.commands.values()],
    }


def root_spec(aliases: Mapping[str, str]) -> dict[str, Any]:
    """Return a root-level machine-readable spec."""

    return {
        "kind": "root",
        "name": "bashon",
        "identity": "agent-first",
        "builtins": [
            {"name": "run", "summary": "Run a target directly."},
            {"name": "add", "summary": "Register an alias for a target."},
            {"name": "list", "summary": "List registered aliases."},
            {"name": "remove", "summary": "Remove a registered alias."},
            {"name": "spec", "summary": "Print command schema for a target."},
        ],
        "aliases": [{"name": alias, "target": target} for alias, target in sorted(aliases.items())],
    }


def render_root_help(aliases: Mapping[str, str]) -> str:
    """Render human help for the root CLI."""

    lines = [
        "Usage: bashon [--human] <command|alias> ...",
        "",
        "Bashon is an agent-first Python-to-CLI toolkit.",
        "",
        "Built-ins:",
        "  run      Run a target directly.",
        "  add      Register an alias for a target.",
        "  list     List registered aliases.",
        "  remove   Remove a registered alias.",
        "  spec     Print command schema for a target.",
    ]
    if aliases:
        lines.extend(["", "Aliases:"])
        for alias, target in sorted(aliases.items()):
            lines.append(f"  {alias:<8} {target}")
    return "\n".join(lines)


def render_collection_help(collection: CommandCollection, *, prog: str) -> str:
    """Render human help for a collection."""

    lines = [f"Usage: {prog} <command> [args...]", "", f"Target: {collection.target.original}", "", "Commands:"]
    for name, command in collection.commands.items():
        lines.append(f"  {name:<18} {command.summary or command.description or command.qualname}")
    return "\n".join(lines)


def emit_success(payload: Any, *, human: bool) -> str:
    """Serialize a successful result for the active mode."""

    if human:
        return _render_human_result(payload)
    envelope = {"ok": True, "mode": "agent", "result": _serialize_jsonable(payload)}
    return json.dumps(envelope, indent=2, sort_keys=True)


def emit_error(error: Exception, *, human: bool) -> str:
    """Serialize an error for the active mode."""

    if human:
        return str(error)
    envelope = {
        "ok": False,
        "mode": "agent",
        "error": {"type": error.__class__.__name__, "message": str(error)},
    }
    return json.dumps(envelope, indent=2, sort_keys=True)


def emit_spec(spec: Mapping[str, Any], *, human: bool, header: str | None = None) -> str:
    """Serialize help/spec output for the active mode."""

    if human:
        return header or json.dumps(spec, indent=2, sort_keys=True)
    envelope = {"ok": True, "mode": "agent", "spec": dict(spec)}
    return json.dumps(envelope, indent=2, sort_keys=True)


def _iter_related_objects(obj: Any) -> Sequence[Any]:
    stack = [obj]
    seen: set[int] = set()
    ordered: list[Any] = []
    while stack:
        current = stack.pop()
        if current is None:
            continue
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        ordered.append(current)
        for attr_name in ("__wrapped__", "__func__", "func"):
            try:
                child = getattr(current, attr_name)
            except Exception:
                child = None
            if child is not None:
                stack.append(child)
    return ordered


def _load_module(target: TargetRef) -> ModuleType:
    if target.kind == "module":
        return importlib.import_module(target.source)

    path = Path(target.source).expanduser().resolve()
    if not path.exists():
        raise CommandNotFoundError(f"File target '{target.source}' does not exist.")
    module_name = f"_bashon_file_{hashlib.sha1(str(path).encode('utf-8')).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise CommandNotFoundError(f"Could not load file target '{target.source}'.")
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(module_name, None)
    sys.modules[module_name] = module
    parent = str(path.parent)
    inserted = False
    if parent and parent not in sys.path:
        sys.path.insert(0, parent)
        inserted = True
    try:
        spec.loader.exec_module(module)
    finally:
        if inserted and sys.path and sys.path[0] == parent:
            sys.path.pop(0)
    return module


def _resolve_path(module: ModuleType, path: str) -> ResolvedObject:
    current: Any = module
    raw: Any = module
    owner_class: type[Any] | None = None
    consumed: list[str] = []
    for part in path.split("."):
        consumed.append(part)
        if inspect.isclass(current):
            owner_class = current
            if part in current.__dict__:
                raw = current.__dict__[part]
            elif hasattr(current, part):
                raw = getattr(current, part)
            else:
                raise CommandNotFoundError(f"'{path}' does not exist in '{module.__name__}'.")
            current = getattr(current, part)
            continue
        if isinstance(current, ModuleType):
            if part not in current.__dict__:
                raise CommandNotFoundError(f"'{path}' does not exist in '{module.__name__}'.")
            raw = current.__dict__[part]
            current = getattr(current, part)
            continue
        if hasattr(current, part):
            raw = getattr(current, part)
            current = raw
            continue
        raise CommandNotFoundError(f"'{path}' does not exist in '{module.__name__}'.")
    qualname = ".".join(consumed)
    return ResolvedObject(raw=raw, bound=current, owner_class=owner_class, qualname=qualname)


def _discover_module_commands(module: ModuleType, source_display: str) -> dict[str, CommandSpec]:
    commands: dict[str, CommandSpec] = {}
    for name, value in module.__dict__.items():
        if name.startswith("_"):
            continue
        if inspect.isclass(value):
            class_commands = _discover_class_commands(value, source_display, skip_unsupported=True)
            for command_name, command in class_commands.items():
                _register_command(commands, command_name, command)
            continue
        if not _get_bashon_meta(value):
            continue
        resolved = ResolvedObject(raw=value, bound=value, owner_class=None, qualname=name)
        try:
            command = _build_command(resolved=resolved, default_name=_slug(name), source_display=source_display)
        except UnsupportedCallableError:
            continue
        _register_command(commands, command.name, command)
    return dict(sorted(commands.items()))


def _discover_class_commands(
    cls: type[Any],
    source_display: str,
    *,
    skip_unsupported: bool,
) -> dict[str, CommandSpec]:
    commands: dict[str, CommandSpec] = {}
    for name, raw in cls.__dict__.items():
        if name.startswith("_"):
            continue
        if not _get_bashon_meta(raw):
            continue
        if not isinstance(raw, (staticmethod, classmethod)):
            if skip_unsupported:
                continue
            raise UnsupportedCallableError(
                f"'{cls.__name__}.{name}' is an instance method or plain class attribute. "
                "Bashon v1 only supports functions, @staticmethod, and @classmethod."
            )
        resolved = ResolvedObject(raw=raw, bound=getattr(cls, name), owner_class=cls, qualname=f"{cls.__name__}.{name}")
        try:
            command = _build_command(
                resolved=resolved,
                default_name=f"{_slug(cls.__name__)}.{_slug(name)}",
                source_display=source_display,
            )
        except UnsupportedCallableError:
            if skip_unsupported:
                continue
            raise
        _register_command(commands, command.name, command)
    return dict(sorted(commands.items()))


def _register_command(commands: dict[str, CommandSpec], name: str, command: CommandSpec) -> None:
    if name in commands:
        raise UnsupportedCallableError(f"Duplicate Bashon command name '{name}'.")
    commands[name] = command


def _build_command_name(resolved: ResolvedObject, fallback: str) -> str:
    if resolved.owner_class is not None:
        return f"{_slug(resolved.owner_class.__name__)}.{_slug(resolved.qualname.split('.')[-1])}"
    return _slug(fallback.split(".")[-1])


def _build_command(resolved: ResolvedObject, default_name: str, source_display: str) -> CommandSpec:
    if resolved.owner_class is not None and not inspect.isclass(resolved.bound) and not isinstance(
        resolved.raw,
        (staticmethod, classmethod),
    ):
        raise UnsupportedCallableError(
            f"'{resolved.qualname}' is an instance method or plain class attribute. "
            "Bashon v1 only supports functions, @staticmethod, and @classmethod."
        )
    meta = _get_bashon_meta(resolved.raw) or BashonMeta()
    langchain_meta = _get_langchain_metadata(resolved.raw)
    signature_target, signature = _resolve_signature_target(resolved)
    invoke_target = _resolve_invoke_target(resolved.raw, resolved.bound)

    if signature.parameters:
        parameters = tuple(_parameter_specs(signature_target, signature, meta, langchain_meta))
    else:
        parameters = ()

    doc = _parse_docstring(signature_target)
    summary = meta.help or doc.summary or langchain_meta.get("description") or ""
    description = meta.help or doc.description or langchain_meta.get("description") or summary
    command_name = meta.name or langchain_meta.get("name") or default_name
    return CommandSpec(
        name=command_name,
        qualname=resolved.qualname,
        summary=summary,
        description=description,
        signature=signature,
        parameters=parameters,
        invoke_target=invoke_target,
        signature_target=signature_target,
        is_langchain_tool=bool(langchain_meta.get("invoke")),
        source=source_display,
    )


def _resolve_signature_target(resolved: ResolvedObject) -> tuple[Callable[..., Any], inspect.Signature]:
    tool_func = getattr(resolved.raw, "func", None)
    if callable(tool_func):
        return inspect.unwrap(tool_func), inspect.signature(tool_func)

    if callable(resolved.bound):
        try:
            bound_signature = inspect.signature(resolved.bound)
        except (TypeError, ValueError):
            bound_signature = None
        if bound_signature is not None and not _signature_is_erased(bound_signature):
            return resolved.bound, bound_signature

    candidates: list[tuple[int, Callable[..., Any], inspect.Signature]] = []
    priority = 0
    for seed in (resolved.bound, resolved.raw):
        for candidate in _iter_related_objects(seed):
            if not callable(candidate):
                continue
            try:
                signature = inspect.signature(candidate)
            except (TypeError, ValueError):
                continue
            candidates.append((priority, candidate, signature))
            priority += 1
    if not candidates:
        raise UnsupportedCallableError(f"'{resolved.qualname}' is not callable.")
    _best_priority, best_candidate, best_signature = max(
        candidates,
        key=lambda item: (_signature_score(item[2]), -item[0]),
    )
    if _signature_is_erased(best_signature):
        raise UnsupportedCallableError(
            f"Could not recover a safe signature for '{resolved.qualname}'. "
            "Decorator stacks must preserve __wrapped__ or __signature__."
        )
    return inspect.unwrap(best_candidate), best_signature


def _resolve_invoke_target(raw: Any, bound: Any) -> Any:
    tool_func = getattr(raw, "func", None)
    if callable(tool_func):
        return tool_func
    if _get_langchain_metadata(raw).get("invoke"):
        return raw
    return bound


def _signature_score(signature: inspect.Signature) -> int:
    score = 0
    for parameter in signature.parameters.values():
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            score -= 2
            continue
        score += 3
        if parameter.annotation is not inspect._empty:
            score += 1
    return score


def _signature_is_erased(signature: inspect.Signature) -> bool:
    if not signature.parameters:
        return False
    return all(
        parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for parameter in signature.parameters.values()
    )


def _parameter_specs(
    callable_obj: Callable[..., Any],
    signature: inspect.Signature,
    meta: BashonMeta,
    langchain_meta: Mapping[str, Any],
) -> list[ParameterSpec]:
    doc = _parse_docstring(callable_obj)
    type_hints = _safe_type_hints(callable_obj)
    params: list[ParameterSpec] = []
    for parameter in signature.parameters.values():
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            raise UnsupportedCallableError(
                f"'{callable_obj.__qualname__}' uses *args or **kwargs, which Bashon does not expose in v1."
            )
        annotation = type_hints.get(parameter.name, parameter.annotation)
        base_annotation, param_meta = _unwrap_annotated(annotation)
        required = parameter.default is inspect._empty
        help_text = param_meta.help or doc.params.get(parameter.name)
        cli_name = _slug(parameter.name)
        aliases = tuple(param_meta.aliases)
        positional = (
            parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            and required
            and not _is_structured_type(base_annotation)
            and not aliases
        )
        is_boolean_flag = (
            not positional
            and _is_bool_annotation(base_annotation)
            and not required
        )
        structured_fields: tuple[StructuredFieldSpec, ...] = ()
        if _is_structured_type(base_annotation):
            structured_fields = tuple(_collect_structured_fields(base_annotation))
        params.append(
            ParameterSpec(
                name=parameter.name,
                cli_name=cli_name,
                annotation=base_annotation,
                required=required,
                default=parameter.default,
                help=help_text,
                aliases=aliases,
                hidden=param_meta.hidden,
                positional=positional,
                is_boolean_flag=is_boolean_flag,
                structured_fields=structured_fields,
            )
        )
    return params


def _safe_type_hints(callable_obj: Callable[..., Any]) -> dict[str, Any]:
    try:
        return get_type_hints(callable_obj, include_extras=True)
    except Exception:
        return {}


def _unwrap_annotated(annotation: Any) -> tuple[Any, Param]:
    if get_origin(annotation) is not Annotated:
        return annotation, Param()
    args = get_args(annotation)
    base = args[0]
    param_meta = next((item for item in args[1:] if isinstance(item, Param)), Param())
    return base, param_meta


def _get_bashon_meta(obj: Any) -> BashonMeta | None:
    for candidate in _iter_related_objects(obj):
        meta = getattr(candidate, BASHON_META_ATTR, None)
        if isinstance(meta, BashonMeta):
            return meta
    return None


def _get_langchain_metadata(obj: Any) -> dict[str, Any]:
    for candidate in _iter_related_objects(obj):
        invoke = getattr(candidate, "invoke", None)
        name = getattr(candidate, "name", None)
        description = getattr(candidate, "description", None)
        if callable(invoke) and (name or description):
            return {
                "invoke": invoke,
                "name": _slug(str(name)) if isinstance(name, str) else None,
                "description": description if isinstance(description, str) else None,
            }
    return {}


def _parse_docstring(obj: Any) -> DocInfo:
    text = inspect.getdoc(obj) or ""
    if not text:
        return DocInfo(summary="", description="", params={})
    lines = text.splitlines()
    summary_lines: list[str] = []
    index = 0
    while index < len(lines) and lines[index].strip():
        summary_lines.append(lines[index].strip())
        index += 1
    summary = " ".join(summary_lines).strip()
    params: dict[str, str] = {}
    description_lines: list[str] = []
    in_arg_block = False
    for raw_line in lines[index:]:
        stripped = raw_line.strip()
        if not stripped:
            if not in_arg_block:
                description_lines.append("")
            continue
        rest_match = _DOC_PARAM_RE.match(stripped)
        if rest_match:
            params[rest_match.group(1)] = rest_match.group(2).strip()
            continue
        if stripped in {"Args:", "Arguments:", "Parameters:", "Params:"}:
            in_arg_block = True
            continue
        if in_arg_block:
            if raw_line.startswith("    ") or raw_line.startswith("\t"):
                doc_match = _DOC_GOOGLE_RE.match(stripped)
                if doc_match:
                    params[doc_match.group(1)] = doc_match.group(2).strip()
                    continue
            in_arg_block = False
        description_lines.append(stripped)
    description = "\n".join(line for line in description_lines).strip() or summary
    return DocInfo(summary=summary, description=description, params=params)


def _is_bool_annotation(annotation: Any) -> bool:
    inner, _ = _unwrap_optional(annotation)
    return inner is bool


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin in (Union, getattr(__import__("types"), "UnionType", Union)):
        args = tuple(arg for arg in get_args(annotation) if arg is not type(None))
        if len(args) == 1 and len(args) != len(get_args(annotation)):
            return args[0], True
    return annotation, False


def _is_structured_type(annotation: Any) -> bool:
    annotation, _ = _unwrap_optional(annotation)
    return _is_dataclass_type(annotation) or _is_pydantic_model_type(annotation)


def _is_dataclass_type(annotation: Any) -> bool:
    return inspect.isclass(annotation) and is_dataclass(annotation)


def _is_pydantic_model_type(annotation: Any) -> bool:
    if not inspect.isclass(annotation):
        return False
    if hasattr(annotation, "model_fields") and callable(getattr(annotation, "model_validate", None)):
        return True
    try:
        from pydantic import BaseModel
    except ImportError:
        return False
    return issubclass(annotation, BaseModel)


def _collect_structured_fields(annotation: Any, prefix: tuple[str, ...] = ()) -> list[StructuredFieldSpec]:
    annotation, _ = _unwrap_optional(annotation)
    collected: list[StructuredFieldSpec] = []
    if _is_dataclass_type(annotation):
        for field_info in fields(annotation):
            base_annotation, _ = _unwrap_annotated(field_info.type)
            help_text = field_info.metadata.get("help") or field_info.metadata.get("description")
            required = field_info.default is MISSING and field_info.default_factory is MISSING
            default = _MISSING
            if field_info.default is not MISSING:
                default = field_info.default
            child_path = prefix + (field_info.name,)
            if _is_structured_type(base_annotation):
                collected.extend(_collect_structured_fields(base_annotation, child_path))
                continue
            collected.append(
                StructuredFieldSpec(
                    path=child_path,
                    annotation=base_annotation,
                    required=required,
                    default=default,
                    help=help_text,
                )
            )
        return collected

    model_fields = getattr(annotation, "model_fields", {})
    for name, field_info in model_fields.items():
        base_annotation, _ = _unwrap_annotated(getattr(field_info, "annotation", Any))
        help_text = getattr(field_info, "description", None)
        is_required = getattr(field_info, "is_required", None)
        required = bool(is_required()) if callable(is_required) else _is_missing(getattr(field_info, "default", _MISSING))
        default = _MISSING if required else getattr(field_info, "default", _MISSING)
        child_path = prefix + (name,)
        if _is_structured_type(base_annotation):
            collected.extend(_collect_structured_fields(base_annotation, child_path))
            continue
        collected.append(
            StructuredFieldSpec(
                path=child_path,
                annotation=base_annotation,
                required=required,
                default=default,
                help=help_text,
            )
        )
    return collected


def _build_structured_argument(parameter: ParameterSpec, namespace: argparse.Namespace) -> Any:
    data: dict[str, Any] = {}
    raw_json = getattr(namespace, f"{parameter.name}__json")
    if raw_json:
        try:
            loaded = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ParseError(f"'{parameter.name}' expects a JSON object.") from exc
        if not isinstance(loaded, dict):
            raise ParseError(f"'{parameter.name}' expects a JSON object.")
        data = loaded
    for field_spec in parameter.structured_fields:
        raw_value = getattr(namespace, f"{parameter.name}__{'__'.join(field_spec.path)}")
        if raw_value is None:
            continue
        _set_nested_value(data, field_spec.path, _coerce_value(field_spec.annotation, raw_value))
    if not data:
        if not _is_missing(parameter.default):
            return parameter.default
        if parameter.required:
            raise ParseError(f"Structured parameter '{parameter.name}' was not provided.")
        return None
    try:
        return _coerce_value(parameter.annotation, data)
    except Exception as exc:
        if isinstance(exc, ParseError):
            raise
        raise ParseError(f"Could not build '{parameter.name}': {exc}") from exc


def _set_nested_value(target: dict[str, Any], path: Sequence[str], value: Any) -> None:
    current = target
    for part in path[:-1]:
        nested = current.get(part)
        if not isinstance(nested, dict):
            nested = {}
            current[part] = nested
        current = nested
    current[path[-1]] = value


def _coerce_value(annotation: Any, value: Any) -> Any:
    base_annotation, _ = _unwrap_annotated(annotation)
    base_annotation, optional = _unwrap_optional(base_annotation)
    if value is None:
        if optional:
            return None
        return None
    if base_annotation in (inspect._empty, Any):
        return value
    if _is_dataclass_type(base_annotation):
        if isinstance(value, base_annotation):
            return value
        if not isinstance(value, Mapping):
            raise ParseError(f"Expected an object for '{base_annotation.__name__}'.")
        kwargs: dict[str, Any] = {}
        for field_info in fields(base_annotation):
            if field_info.name not in value:
                continue
            kwargs[field_info.name] = _coerce_value(field_info.type, value[field_info.name])
        return base_annotation(**kwargs)
    if _is_pydantic_model_type(base_annotation):
        if isinstance(value, base_annotation):
            return value
        if not isinstance(value, Mapping):
            raise ParseError(f"Expected an object for '{base_annotation.__name__}'.")
        if callable(getattr(base_annotation, "model_validate", None)):
            return base_annotation.model_validate(value)
        return base_annotation(**value)
    origin = get_origin(base_annotation)
    if origin in (list, tuple, set):
        items = value
        if isinstance(value, str):
            items = json.loads(value)
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
            raise ParseError(f"Expected a JSON array for '{_type_label(base_annotation)}'.")
        item_type = get_args(base_annotation)[0] if get_args(base_annotation) else Any
        converted = [_coerce_value(item_type, item) for item in items]
        if origin is tuple:
            return tuple(converted)
        if origin is set:
            return set(converted)
        return converted
    if origin in (dict, Mapping):
        mapping = value
        if isinstance(value, str):
            mapping = json.loads(value)
        if not isinstance(mapping, Mapping):
            raise ParseError(f"Expected a JSON object for '{_type_label(base_annotation)}'.")
        key_type, value_type = (get_args(base_annotation) + (Any, Any))[:2]
        return {
            _coerce_value(key_type, key): _coerce_value(value_type, item)
            for key, item in mapping.items()
        }
    if base_annotation is bool:
        return _parse_bool(value)
    if base_annotation is str:
        return str(value)
    if base_annotation is int:
        return int(value)
    if base_annotation is float:
        return float(value)
    if inspect.isclass(base_annotation) and issubclass(base_annotation, Path):
        return base_annotation(value)
    if inspect.isclass(base_annotation) and issubclass(base_annotation, Enum):
        try:
            return base_annotation[value]
        except Exception:
            return base_annotation(value)
    return value


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ParseError(f"Expected a boolean value, got '{value}'.")


def _type_label(annotation: Any) -> str:
    base_annotation, _ = _unwrap_optional(annotation)
    origin = get_origin(base_annotation)
    if origin:
        args = ", ".join(_type_label(arg) for arg in get_args(base_annotation))
        return f"{origin.__name__}[{args}]"
    if getattr(base_annotation, "__name__", None):
        return base_annotation.__name__
    return str(base_annotation)


def _slug(value: str) -> str:
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", value)
    second = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", first)
    return second.replace("_", "-").lower()


def _parameter_to_spec(parameter: ParameterSpec) -> dict[str, Any]:
    payload = {
        "name": parameter.name,
        "cli_name": parameter.cli_name,
        "type": _type_label(parameter.annotation),
        "required": parameter.required,
        "help": parameter.help,
        "aliases": list(parameter.aliases),
        "kind": "structured" if parameter.is_structured else ("flag" if parameter.is_boolean_flag else ("positional" if parameter.positional else "option")),
    }
    if not _is_missing(parameter.default):
        payload["default"] = _serialize_jsonable(parameter.default)
    if parameter.is_structured:
        payload["fields"] = [
            {
                "path": list(field_spec.path),
                "cli_name": f"{parameter.cli_name}.{field_spec.cli_suffix}",
                "type": _type_label(field_spec.annotation),
                "required": field_spec.required,
                "help": field_spec.help,
                **({"default": _serialize_jsonable(field_spec.default)} if not _is_missing(field_spec.default) else {}),
            }
            for field_spec in parameter.structured_fields
        ]
    return payload


def _serialize_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SerializationError("Could not serialize bytes as UTF-8.") from exc
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return _serialize_jsonable(value.value)
    if is_dataclass(value):
        return {key: _serialize_jsonable(item) for key, item in asdict(value).items()}
    if _is_pydantic_model_type(type(value)):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _serialize_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_jsonable(item) for item in value]
    raise SerializationError(f"Could not serialize value of type '{type(value).__name__}'.")


def _render_human_result(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, bytes):
        return payload.decode("utf-8")
    if is_dataclass(payload) or isinstance(payload, (Mapping, list, tuple, set)):
        return json.dumps(_serialize_jsonable(payload), indent=2, sort_keys=True)
    return str(payload)
