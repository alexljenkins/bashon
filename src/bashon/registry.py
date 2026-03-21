"""Alias registry management."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .errors import AliasError


BUILTIN_NAMES = {"run", "add", "list", "remove", "spec", "help"}


def _config_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("APPDATA")
        if root:
            return Path(root) / "bashon"
    if os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / "bashon"
    if os.name == "posix" and os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / "bashon"
    return Path.home() / ".config" / "bashon"


@dataclass
class AliasRegistry:
    """Persistent alias registry."""

    path: Path = field(default_factory=lambda: _config_dir() / "registry.json")
    aliases: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "AliasRegistry":
        path = _config_dir() / "registry.json"
        if not path.exists():
            return cls(path=path, aliases={})
        data = json.loads(path.read_text(encoding="utf-8"))
        aliases = data.get("aliases", {})
        if not isinstance(aliases, dict):
            raise AliasError("Registry file is invalid.")
        return cls(path=path, aliases={str(key): str(value) for key, value in aliases.items()})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "aliases": dict(sorted(self.aliases.items()))}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def add(self, alias: str, target: str) -> None:
        if alias in BUILTIN_NAMES:
            raise AliasError(f"'{alias}' is reserved by Bashon.")
        self.aliases[alias] = target
        self.save()

    def remove(self, alias: str) -> None:
        if alias not in self.aliases:
            raise AliasError(f"Alias '{alias}' is not registered.")
        del self.aliases[alias]
        self.save()
