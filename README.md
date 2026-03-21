# bashon

Bashon turns decorated Python callables into agent-first CLI commands with almost no runtime dependencies.

```python
from dataclasses import dataclass, field
from bashon import bashon


@dataclass
class User:
    name: str = field(metadata={"help": "Display name"})
    age: int = field(default=0, metadata={"help": "Age in years"})


@bashon
def greet(user: User, excited: bool = False) -> dict[str, object]:
    """Greet a user.

    Args:
        user: User details for the greeting.
        excited: Add extra emphasis.
    """
    message = f"Hello, {user.name}"
    if excited:
        message += "!"
    return {"message": message, "age": user.age}
```

Run it directly:

```bash
bashon run path/to/file.py:greet --user.name Alex --user.age 30 --excited
```

Register it for repeated use:

```bash
bashon add greet path/to/file.py:greet
bashon greet --user '{"name":"Alex","age":30}'
```

Agent mode is the default. Use `--human` for conventional terminal help and output:

```bash
bashon --human run path/to/file.py:greet --help
```
