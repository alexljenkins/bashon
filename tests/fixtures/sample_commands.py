"""Sample commands for Bashon tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from bashon import Param, bashon
from langchain_core.tools import tool
from pydantic import BaseModel, Field


@bashon
def greet(name: str, excited: bool = False) -> str:
    """Greet a user.

    Args:
        name: The name to greet.
        excited: Add extra emphasis.
    """

    punctuation = "!" if excited else "."
    return f"Hello, {name}{punctuation}"


@dataclass
class User:
    """Structured user input."""

    name: str = field(metadata={"help": "Display name"})
    age: int = field(default=0, metadata={"help": "Age in years"})


@bashon
def describe(user: User) -> dict[str, object]:
    """Describe a structured user."""

    return {"name": user.name, "age": user.age}


class Profile(BaseModel):
    """Structured Pydantic input."""

    name: str = Field(description="Profile name")
    age: int = Field(default=0, description="Profile age")


@bashon
def describe_profile(profile: Profile) -> dict[str, object]:
    """Describe a Pydantic profile."""

    return profile.model_dump(mode="json")


@bashon
def nicknamed(name: Annotated[str, Param(help="Primary name", aliases=("--person",))]) -> str:
    """Echo a name using Annotated metadata."""

    return name


class Ops:
    """Class-based command fixtures."""

    @classmethod
    @bashon
    def ping(cls, count: int = 1) -> str:
        """Ping a class method."""

        return f"{cls.__name__}:{count}"

    @bashon
    @classmethod
    def pong(cls, count: int = 1) -> str:
        """Pong a class method with reversed decorator order."""

        return f"{cls.__name__}:{count}:pong"

    @staticmethod
    @bashon
    def echo(text: str) -> str:
        """Echo text."""

        return text

    @bashon
    @staticmethod
    def reverse(text: str) -> str:
        """Reverse text."""

        return text[::-1]

    @bashon
    def not_supported(self, value: int) -> int:
        """Unsupported instance method."""

        return value


def wrapper_without_wraps(func):
    def inner(*args, **kwargs):
        return func(*args, **kwargs)

    return inner


@bashon
@wrapper_without_wraps
def opaque(name: str) -> str:
    """Opaque wrapped function."""

    return name


@bashon
@tool
def lang_a(value: int) -> int:
    """Increment with tool outer metadata."""

    return value + 1


@tool
@bashon
def lang_b(value: int) -> int:
    """Increment with tool inner metadata."""

    return value + 2
