# bashon

Turn any Python function into a CLI command. One decorator. That's it.

```python
from bashon import bashon

@bashon
def greet(name: str, excited: bool = False) -> str:
    """Greet somebody."""
    return f"Hello, {name}{'!' if excited else '.'}"
```

```bash
$ bashon run hello.py:greet Ada --excited
```
```json
{"ok": true, "result": "Hello, Ada!"}
```

## Why bashon?

Most CLI frameworks make you write a bunch of scaffolding just to call a function from the terminal. Bashon skips all that. You already have typed Python functions with docstrings — that's enough.

And here's the thing that makes it different: **bashon is agent-first**. The default output is structured JSON, ready for AI agents, pipelines, and tooling to consume. Add `--human` when *you* want to read it. Most CLI tools do it the other way around — human-first, then you bolt on machine-readable output as an afterthought.

Zero runtime dependencies. Python 3.10+.

## Install

```bash
pip install bashon
```

## Quick start

Write a function, decorate it, run it:

```python
# hello.py
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

```bash
# Agent mode (default) — structured JSON
$ bashon run hello.py:greet Ada
{"ok": true, "result": "Hello, Ada."}

# Human mode — just the result
$ bashon --human run hello.py:greet Ada --excited
Hello, Ada!

# Help text — pulled straight from your docstring
$ bashon --human run hello.py:greet --help
Usage: bashon run hello.py:greet [--help] [--excited | --no-excited] name

Greet somebody.

positional arguments:
  name                  Who to greet.

options:
  --help                Show help.
  --excited, --no-excited
                        Add extra emphasis.
```

That's the whole workflow. Type hints become arguments. Docstrings become help text. Defaults become optional flags.

## Agent mode vs human mode

Agent mode is the default because bashon is built for automation first:

| | Agent mode (default) | Human mode (`--human`) |
|---|---|---|
| Success | JSON envelope | Plain text or pretty-printed JSON |
| Errors | Structured JSON | Readable message |
| Schema | `bashon spec` | `bashon --human spec` |

## Built-in commands

```bash
bashon run TARGET [ARGS...]      # Run a command
bashon spec TARGET               # Print the schema (great for agents)
bashon add ALIAS TARGET          # Save a shortcut
bashon list                      # Show saved shortcuts
bashon remove ALIAS              # Delete a shortcut
```

Targets can be file paths or module paths:

```bash
bashon run hello.py:greet Ada
bashon run mypackage.commands:deploy --env prod
```

---

## Going further

### Structured inputs with dataclasses

When a parameter is a dataclass, bashon lets you pass it as JSON or as flattened flags:

```python
from dataclasses import dataclass, field
from bashon import bashon

@dataclass
class User:
    name: str = field(metadata={"help": "Display name"})
    age: int = field(default=0, metadata={"help": "Age in years"})

@bashon
def describe(user: User) -> dict:
    """Describe a user."""
    return {"name": user.name, "age": user.age}
```

```bash
# Flattened flags
$ bashon run app.py:describe --user.name Ada --user.age 30

# Or JSON
$ bashon run app.py:describe --user '{"name": "Ada", "age": 30}'

# Mix both — flags override JSON values
$ bashon run app.py:describe --user '{"name": "Wrong", "age": 30}' --user.name Ada
```

### Pydantic models

Same deal, works out of the box:

```python
from bashon import bashon
from pydantic import BaseModel, Field

class Profile(BaseModel):
    name: str = Field(description="Profile name")
    age: int = Field(default=0, description="Profile age")

@bashon
def describe_profile(profile: Profile) -> dict:
    """Describe a profile."""
    return profile.model_dump(mode="json")
```

```bash
$ bashon run app.py:describe_profile --profile.name Ada --profile.age 5
```

### Parameter aliases with `Annotated`

Use `Param` for aliases or explicit help on individual parameters:

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

```bash
$ bashon run app.py:nicknamed --person Ada
```

### Command groups

If a module has multiple `@bashon` functions, bashon treats it as a command group:

```bash
$ bashon --human spec mymodule.py
```

This lists all discovered commands with their summaries.

### Class-based commands

`@staticmethod` and `@classmethod` work too:

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

```bash
$ bashon run app.py:Ops.echo hello
$ bashon run app.py:Ops.ping --count 3
```

### LangChain interop

Reuse the same function as a CLI command *and* a LangChain tool. Both decorator orders work:

```python
from bashon import bashon
from langchain_core.tools import tool

@bashon
@tool
def add_one(value: int) -> int:
    """Increment a value."""
    return value + 1
```

```bash
$ bashon run app.py:add_one 1
```

One function. CLI command. Agent tool. LangChain tool. Done.

### Aliases

Save shortcuts for commands you run often:

```bash
$ bashon add hello hello.py:greet
$ bashon hello Ada
$ bashon remove hello
```

### Custom command names

Override the default naming (which converts `my_function` to `my-function`):

```python
@bashon(name="hello")
def greet(name: str) -> str:
    return f"Hello, {name}"
```

### Machine-readable schema

`bashon spec` gives agents everything they need to call your commands:

```bash
$ bashon spec hello.py:greet
```

```json
{
  "ok": true,
  "spec": {
    "commands": [{
      "name": "greet",
      "description": "Greet somebody.",
      "parameters": [
        {"name": "name", "type": "str", "required": true, "help": "Who to greet."},
        {"name": "excited", "type": "bool", "required": false, "default": false, "help": "Add extra emphasis."}
      ]
    }]
  }
}
```
