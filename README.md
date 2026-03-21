# bashon

Bashon turns decorated Python callables into CLI commands with very little setup. Basically a Python to CLI package to allow simple execution of python code by your AI agents.

It is built for two modes:

- Agent mode by default, where output is JSON and easy for tools or agents to consume.
- Human mode with `--human`, where help and results look like a conventional CLI.

## What Bashon gives you

- Decorate a function with `@bashon`
- Run it directly from a file or module
- Get argument parsing from Python type hints
- Get help text from docstrings and field metadata
- Expose structured inputs from dataclasses and Pydantic models
- Reuse the same callable as a Bashon command and a LangChain `@tool`

## Install

For local development in this repo:

```bash
uv pip install -e .
```

Or with pip:

```bash
pip install -e .
```

Then the `bashon` command is available on your path.

## The simplest possible example

Start with any python function you currently have... and add the `bashon` decorator.

```python
from bashon import bashon


@bashon
def greet(name: str, excited: bool = False) -> str:
    """Greet somebody.

    Args:
        name: Who to greet.
        excited: Add extra emphasis.
    """
    return f"Hello, {name}{'!' if excited else '.'}"
```

Then run it directly from the terminal with:

```bash
bashon run hello.py:greet Ada
```

Output in the default agent mode:

```json
{
  "mode": "agent",
  "ok": true,
  "result": "Hello, Ada."
}
```

Turn on the boolean flag:

```bash
bashon --human run hello.py:greet Ada --excited
```

Human-friendly output:

```text
Hello, Ada!
```

Ask Bashon for help:

```bash
bashon --human run hello.py:greet --help
```

Help output:

```text
Usage: bashon run hello.py:greet [--help]
                                 [--excited | --no-excited]
                                 name

Greet somebody.

positional arguments:
  name                  Who to greet.

options:
  --help                Show help.
  --excited, --no-excited
                        Add extra emphasis.
```

You can also inspect the machine-readable schema:

```bash
bashon spec hello.py:greet
```

Example output:

```json
{
  "mode": "agent",
  "ok": true,
  "spec": {
    "commands": [
      {
        "callable": "greet",
        "description": "Greet somebody.",
        "kind": "command",
        "name": "greet",
        "parameters": [
          {
            "aliases": [],
            "cli_name": "name",
            "help": "Who to greet.",
            "kind": "positional",
            "name": "name",
            "required": true,
            "type": "str"
          },
          {
            "aliases": [],
            "cli_name": "excited",
            "default": false,
            "help": "Add extra emphasis.",
            "kind": "flag",
            "name": "excited",
            "required": false,
            "type": "bool"
          }
        ],
        "source": "hello.py:greet",
        "summary": "Greet somebody."
      }
    ],
    "kind": "collection",
    "target": "hello.py:greet"
  }
}
```

## Running commands

There are two common ways to run a command:

```bash
bashon run path/to/file.py:callable
bashon run some.module.path:callable
```

If a module or file exposes multiple `@bashon` commands, Bashon treats it like a command group:

```bash
bashon --human spec some.module.path
```

That will list the discovered command names and summaries.

## Human mode vs agent mode

Agent mode is the default:

- Success output is wrapped in a JSON envelope
- Errors are returned as structured JSON
- Specs are emitted as JSON

Human mode is enabled with `--human`:

- Strings print as plain text
- Mappings, dataclasses, and lists print as pretty JSON
- Help text uses standard CLI formatting

That makes Bashon work well both for terminals and for agent/tooling workflows.

## Structured inputs with dataclasses

When a parameter is a dataclass, Bashon exposes it in two ways:

- As one JSON object argument like `--user '{"name":"Ada","age":30}'`
- As flattened field flags like `--user.name Ada --user.age 30`

Example:

```python
from dataclasses import dataclass, field

from bashon import bashon


@dataclass
class User:
    name: str = field(metadata={"help": "Display name"})
    age: int = field(default=0, metadata={"help": "Age in years"})


@bashon
def describe(user: User) -> dict[str, object]:
    """Describe a structured user."""
    return {"name": user.name, "age": user.age}
```

Run it with field flags:

```bash
bashon run app.py:describe --user.name Ada --user.age 30
```

Or with JSON:

```bash
bashon --human run app.py:describe --user '{"name":"Ada","age":30}'
```

And you can mix them. Field flags win over JSON values when both are provided:

```bash
bashon run app.py:describe \
  --user '{"name":"Wrong","age":4}' \
  --user.name Ada
```

That produces:

```json
{
  "mode": "agent",
  "ok": true,
  "result": {
    "age": 4,
    "name": "Ada"
  }
}
```

This is especially handy when an agent or shell script wants to override only one nested value.

## Pydantic models and Pydantic dataclasses

Bashon also supports structured inputs backed by Pydantic models, using the same flattened CLI shape:

```python
from bashon import bashon
from pydantic import BaseModel, Field


class Profile(BaseModel):
    name: str = Field(description="Profile name")
    age: int = Field(default=0, description="Profile age")


@bashon
def describe_profile(profile: Profile) -> dict[str, object]:
    """Describe a profile."""
    return profile.model_dump(mode="json")
```

Run it like this:

```bash
bashon run app.py:describe_profile --profile.name Ada --profile.age 5
```

Or with a JSON object:

```bash
bashon run app.py:describe_profile --profile '{"name":"Ada","age":5}'
```

Pydantic dataclasses fit naturally into the same structured-input flow because Bashon already knows how to flatten dataclass fields.

## Where help text comes from

Bashon pulls help from a few places:

- Function docstrings for command summaries and parameter descriptions
- Dataclass field metadata like `field(metadata={"help": "..."})`
- Pydantic field descriptions like `Field(description="...")`
- `typing.Annotated[..., Param(...)]` metadata

That means you can keep documentation close to the code and have it show up in both:

- `bashon --human run ... --help`
- `bashon spec ...`

## Custom parameter metadata with `Annotated`

Use `bashon.Param` with `typing.Annotated` when you want aliases or explicit help on a scalar parameter.

```python
from typing import Annotated

from bashon import Param, bashon


@bashon
def nicknamed(
    name: Annotated[str, Param(help="Primary name", aliases=("--person",))]
) -> str:
    """Echo a name."""
    return name
```

Now both of these work:

```bash
bashon run app.py:nicknamed Ada
bashon run app.py:nicknamed --person Ada
```

## Registering aliases

If you want a stable local command name, register an alias:

```bash
bashon add hello hello.py:greet
```

Result:

```json
{
  "mode": "agent",
  "ok": true,
  "result": {
    "alias": "hello",
    "status": "added",
    "target": "hello.py:greet"
  }
}
```

Then run it directly:

```bash
bashon hello Ada
```

List aliases:

```bash
bashon list
```

Remove one:

```bash
bashon remove hello
```

## Class-based commands

Bashon can discover commands from classes too, as long as they are `@staticmethod` or `@classmethod`.

```python
from bashon import bashon


class Ops:
    @staticmethod
    @bashon
    def echo(text: str) -> str:
        return text

    @classmethod
    @bashon
    def ping(cls, count: int = 1) -> str:
        return f"{cls.__name__}:{count}"
```

Those become commands like:

```bash
bashon run app.py:Ops.echo hello
bashon run app.py:Ops.ping --count 3
```

Instance methods are intentionally not exposed.

## Using Bashon with LangChain `@tool`

One of the nicest Bashon workflows is reusing the same function as both:

- A Bashon CLI command
- A LangChain tool

Bashon supports both decorator orders:

```python
from bashon import bashon
from langchain_core.tools import tool


@bashon
@tool
def add_one(value: int) -> int:
    """Increment a value."""
    return value + 1


@tool
@bashon
def add_two(value: int) -> int:
    """Increment a value by two."""
    return value + 2
```

Both can be run by Bashon:

```bash
bashon run app.py:add_one 1
bashon run app.py:add_two 1
```

This makes it easy to define a function once and reuse it everywhere:

- In local CLI workflows
- In agents
- In LangChain tool stacks

## How command naming works

By default, Bashon converts names to CLI-friendly slugs:

- `my_function` becomes `my-function`
- `MyClass.runTask` becomes `my-class.run-task`

You can override the command name in the decorator:

```python
from bashon import bashon


@bashon(name="hello")
def greet(name: str) -> str:
    return f"Hello, {name}"
```

## Built-in commands

Bashon ships with a small built-in command set:

- `bashon run TARGET [ARGS...]`
- `bashon spec TARGET [COMMAND]`
- `bashon add ALIAS TARGET`
- `bashon list`
- `bashon remove ALIAS`

Run root help any time with:

```bash
bashon --human
```

## Design notes

Bashon currently focuses on a tight, predictable surface area:

- Functions are supported
- `@staticmethod` and `@classmethod` are supported
- Instance methods are not supported
- `*args` and `**kwargs` are not exposed
- Boolean options use `--flag` and `--no-flag`

That narrow scope helps keep discovery, parsing, help text, and structured schemas reliable.

## A practical pattern

A good default pattern is:

1. Start with a plain typed function and `@bashon`
2. Add a docstring so help output is useful
3. Move to a dataclass or Pydantic model when the input becomes structured
4. Add `@tool` if you also want to use the same callable in LangChain
5. Register a `bashon add` alias if you run it often

That gives you one Python definition that can serve as:

- A local CLI command
- A machine-readable command for agents
- A reusable tool in a larger system
