"""CLI tests for Bashon."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from bashon.cli import main


FIXTURE_MODULE = "tests.fixtures.sample_commands"
FIXTURE_FILE = Path(__file__).resolve().parent / "fixtures" / "sample_commands.py"


def run_cli(argv: list[str], *, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    previous = {}
    if env:
        for key, value in env.items():
            previous[key] = os.environ.get(key)
            os.environ[key] = value
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv)
    finally:
        if env:
            for key, old_value in previous.items():
                if old_value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old_value
    return code, stdout.getvalue(), stderr.getvalue()


def parse_agent_output(stdout: str) -> dict[str, object]:
    """Parse text-delimited agent output into a dict.

    Handles:
      [BASHON:OK] / [BASHON:ERROR] as status
      [RESULT_TYPE:...] / [ERROR_TYPE:...] / [SPEC] as metadata
      Everything after the marker lines as the body
    """
    lines = stdout.strip().split("\n")
    result: dict[str, object] = {}

    if not lines:
        return result

    # First line: status
    first = lines[0]
    if first == "[BASHON:OK]":
        result["ok"] = True
    elif first == "[BASHON:ERROR]":
        result["ok"] = False
    else:
        raise ValueError(f"Unexpected first line: {first!r}")

    # Consume marker lines
    body_start = 1
    for i, line in enumerate(lines[1:], start=1):
        if line.startswith("[") and line.endswith("]") and ":" in line:
            key, _, val = line[1:-1].partition(":")
            result[key.lower()] = val
            body_start = i + 1
        elif line == "[SPEC]":
            result["spec"] = True
            body_start = i + 1
        else:
            body_start = i
            break

    body = "\n".join(lines[body_start:])
    if body:
        # Try parsing as JSON, fall back to plain text
        try:
            result["body"] = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            result["body"] = body

    return result


class BashonCliTests(unittest.TestCase):
    # ── Default agent text-delimited format ──────────────────────────

    def test_agent_mode_runs_function(self) -> None:
        code, stdout, stderr = run_cli(["run", f"{FIXTURE_MODULE}:greet", "Alex", "--excited"])
        self.assertEqual(code, 0, stderr)
        out = parse_agent_output(stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["result_type"], "string")
        self.assertEqual(out["body"], "Hello, Alex!")

    def test_agent_mode_returns_object(self) -> None:
        code, stdout, stderr = run_cli(
            ["run", f"{FIXTURE_MODULE}:describe", "--user", '{"name":"Alex","age":5}']
        )
        self.assertEqual(code, 0, stderr)
        out = parse_agent_output(stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["result_type"], "object")
        self.assertEqual(out["body"], {"age": 5, "name": "Alex"})

    def test_agent_mode_returns_array(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"XDG_CONFIG_HOME": temp_dir}
            run_cli(["add", "hello", f"{FIXTURE_MODULE}:greet"], env=env)
            code, stdout, stderr = run_cli(["list"], env=env)
            self.assertEqual(code, 0, stderr)
            out = parse_agent_output(stdout)
            self.assertTrue(out["ok"])
            self.assertEqual(out["result_type"], "array")

    def test_agent_mode_error(self) -> None:
        code, stdout, stderr = run_cli(["run", "nonexistent:nope"])
        self.assertEqual(code, 1)
        out = parse_agent_output(stdout)
        self.assertFalse(out["ok"])
        self.assertIn(out["error_type"], ("CommandNotFoundError", "ModuleNotFoundError"))

    def test_agent_mode_spec(self) -> None:
        code, stdout, stderr = run_cli(["spec", FIXTURE_MODULE])
        self.assertEqual(code, 0, stderr)
        out = parse_agent_output(stdout)
        self.assertTrue(out["ok"])
        self.assertTrue(out.get("spec"))
        names = {cmd["name"] for cmd in out["body"]["commands"]}
        self.assertIn("greet", names)

    # ── Human mode ───────────────────────────────────────────────────

    def test_human_help_for_command(self) -> None:
        code, stdout, stderr = run_cli(["--human", "run", f"{FIXTURE_MODULE}:greet", "--help"])
        self.assertEqual(code, 0, stderr)
        self.assertIn("Usage:", stdout)
        self.assertIn("--excited", stdout)
        self.assertIn("name", stdout)

    def test_human_flag_alias(self) -> None:
        code, stdout, stderr = run_cli(["--format", "human", "run", f"{FIXTURE_MODULE}:greet", "Alex"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout.strip(), "Hello, Alex.")

    # ── JSON format (backward compat) ────────────────────────────────

    def test_json_format_runs_function(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "run", f"{FIXTURE_MODULE}:greet", "Alex", "--excited"])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"], "Hello, Alex!")
        self.assertEqual(payload["result_type"], "string")
        self.assertEqual(payload["__bashon__"], "1")

    def test_json_format_error(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "run", "nonexistent:nope"])
        self.assertEqual(code, 1)
        payload = json.loads(stdout)
        self.assertFalse(payload["ok"])
        self.assertIn(payload["error"]["type"], ("CommandNotFoundError", "ModuleNotFoundError"))
        self.assertEqual(payload["__bashon__"], "1")

    def test_json_format_spec(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "spec", FIXTURE_MODULE])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["ok"])
        self.assertIn("commands", payload["spec"])
        self.assertEqual(payload["__bashon__"], "1")

    # ── Structured input / specs ─────────────────────────────────────

    def test_structured_input_flags_override_json(self) -> None:
        code, stdout, stderr = run_cli(
            [
                "--format", "json",
                "run",
                f"{FIXTURE_MODULE}:describe",
                "--user",
                '{"name":"Wrong","age":4}',
                "--user.name",
                "Alex",
            ]
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["result"], {"age": 4, "name": "Alex"})

    def test_spec_in_agent_mode_contains_structured_fields(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "spec", f"{FIXTURE_MODULE}:describe"])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        fields = payload["spec"]["commands"][0]["parameters"][0]["fields"]
        self.assertEqual([field["cli_name"] for field in fields], ["user.name", "user.age"])

    def test_pydantic_model_input_and_spec(self) -> None:
        code, stdout, stderr = run_cli(
            ["--format", "json", "run", f"{FIXTURE_MODULE}:describe_profile", "--profile.name", "Alex", "--profile.age", "5"]
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"], {"age": 5, "name": "Alex"})

        code, stdout, stderr = run_cli(["--format", "json", "spec", f"{FIXTURE_MODULE}:describe_profile"])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        fields = payload["spec"]["commands"][0]["parameters"][0]["fields"]
        self.assertEqual([field["cli_name"] for field in fields], ["profile.name", "profile.age"])

    def test_module_target_becomes_group(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "spec", FIXTURE_MODULE])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        names = {command["name"] for command in payload["spec"]["commands"]}
        self.assertIn("greet", names)
        self.assertIn("ops.echo", names)
        self.assertIn("ops.ping", names)

    # ── Class methods / static methods ───────────────────────────────

    def test_classmethod_and_staticmethod_execution(self) -> None:
        code, stdout, stderr = run_cli(["run", f"{FIXTURE_MODULE}:Ops.echo", "hello"])
        self.assertEqual(code, 0, stderr)
        out = parse_agent_output(stdout)
        self.assertEqual(out["body"], "hello")

        code, stdout, stderr = run_cli(["run", f"{FIXTURE_MODULE}:Ops.ping", "--count", "3"])
        self.assertEqual(code, 0, stderr)
        out = parse_agent_output(stdout)
        self.assertEqual(out["body"], "Ops:3")

    def test_instance_method_is_rejected(self) -> None:
        code, stdout, _ = run_cli(["run", f"{FIXTURE_MODULE}:Ops.not_supported", "3"])
        self.assertEqual(code, 1)
        out = parse_agent_output(stdout)
        self.assertIn("only supports functions, @staticmethod, and @classmethod", out["body"])

    def test_unrecoverable_wrapper_error_is_clear(self) -> None:
        code, stdout, _ = run_cli(["run", f"{FIXTURE_MODULE}:opaque", "Alex"])
        self.assertEqual(code, 1)
        out = parse_agent_output(stdout)
        self.assertIn("preserve __wrapped__ or __signature__", out["body"])

    # ── LangChain tools ──────────────────────────────────────────────

    def test_langchain_like_tools_work_in_both_orders(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "run", f"{FIXTURE_MODULE}:lang_a", "1"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"], 2)

        code, stdout, stderr = run_cli(["--format", "json", "run", f"{FIXTURE_MODULE}:lang_b", "1"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"], 3)

    # ── Alias lifecycle ──────────────────────────────────────────────

    def test_alias_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"XDG_CONFIG_HOME": temp_dir}
            code, stdout, stderr = run_cli(["--format", "json", "add", "hello", f"{FIXTURE_MODULE}:greet"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["result"]["status"], "added")

            code, stdout, stderr = run_cli(["--format", "json", "hello", "Alex"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["result"], "Hello, Alex.")

            code, stdout, stderr = run_cli(["--format", "json", "list"], env=env)
            self.assertEqual(code, 0, stderr)
            listing = json.loads(stdout)["result"]
            self.assertEqual(listing, [{"alias": "hello", "target": f"{FIXTURE_MODULE}:greet"}])

            code, stdout, stderr = run_cli(["--format", "json", "remove", "hello"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["result"]["status"], "removed")

    def test_alias_conflict_with_builtin_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            code, stdout, _ = run_cli(["--format", "json", "add", "run", f"{FIXTURE_MODULE}:greet"], env={"XDG_CONFIG_HOME": temp_dir})
            self.assertEqual(code, 1)
            payload = json.loads(stdout)
            self.assertIn("reserved by Bashon", payload["error"]["message"])

    # ── File target ──────────────────────────────────────────────────

    def test_file_target_executes(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "run", f"{FIXTURE_FILE}:greet", "Alex"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"], "Hello, Alex.")

    # ── Annotated param alias ────────────────────────────────────────

    def test_annotated_param_alias_is_supported(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "run", f"{FIXTURE_MODULE}:nicknamed", "--person", "Alex"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"], "Alex")

    # ── New feature: return_type in spec ─────────────────────────────

    def test_spec_includes_return_type(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "spec", f"{FIXTURE_MODULE}:greet"])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        spec = payload["spec"]["commands"][0]
        self.assertEqual(spec["return_type"], "str")

    def test_spec_return_type_for_dict(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "spec", f"{FIXTURE_MODULE}:describe"])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        spec = payload["spec"]["commands"][0]
        self.assertIn("return_type", spec)

    # ── New feature: JSON string unwrapping ──────────────────────────

    def test_json_string_unwrapped(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "run", f"{FIXTURE_MODULE}:json_returner"])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        # Should be unwrapped from string to object
        self.assertEqual(payload["result"], {"key": "value", "number": 42})
        self.assertEqual(payload["result_type"], "object")

    # ── New feature: result_type ─────────────────────────────────────

    def test_result_type_string(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "run", f"{FIXTURE_MODULE}:greet", "Alex"])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["result_type"], "string")

    def test_result_type_integer(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "run", f"{FIXTURE_MODULE}:lang_a", "1"])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["result_type"], "integer")

    def test_result_type_null(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "run", f"{FIXTURE_MODULE}:void_func"])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["result_type"], "null")

    # ── New feature: structured error fields ─────────────────────────

    def test_error_includes_parameter_context(self) -> None:
        # Use required_option which has a non-positional required param
        code, stdout, stderr = run_cli(["--format", "json", "run", f"{FIXTURE_MODULE}:required_option"])
        self.assertEqual(code, 1)
        payload = json.loads(stdout)
        self.assertFalse(payload["ok"])
        error = payload["error"]
        self.assertEqual(error["type"], "ParseError")
        self.assertEqual(error["parameter"], "count")
        self.assertEqual(error["expected_type"], "int")

    def test_error_context_in_agent_text(self) -> None:
        code, stdout, stderr = run_cli(["run", f"{FIXTURE_MODULE}:required_option"])
        self.assertEqual(code, 1)
        out = parse_agent_output(stdout)
        self.assertFalse(out["ok"])
        self.assertEqual(out.get("parameter"), "count")
        self.assertEqual(out.get("expected_type"), "int")

    # ── New feature: catch-all handler ───────────────────────────────

    def test_catchall_handles_runtime_error(self) -> None:
        code, stdout, stderr = run_cli(["--format", "json", "run", f"{FIXTURE_MODULE}:exploder"])
        self.assertEqual(code, 1)
        payload = json.loads(stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "RuntimeError")
        self.assertIn("boom", payload["error"]["message"])

    def test_catchall_in_agent_text(self) -> None:
        code, stdout, stderr = run_cli(["run", f"{FIXTURE_MODULE}:exploder"])
        self.assertEqual(code, 1)
        out = parse_agent_output(stdout)
        self.assertFalse(out["ok"])
        self.assertEqual(out.get("error_type"), "RuntimeError")

    # ── Invalid format flag ──────────────────────────────────────────

    def test_invalid_format_flag(self) -> None:
        code, stdout, stderr = run_cli(["--format", "yaml", "run", f"{FIXTURE_MODULE}:greet", "Alex"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
