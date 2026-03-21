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


class BashonCliTests(unittest.TestCase):
    def test_agent_mode_runs_function(self) -> None:
        code, stdout, stderr = run_cli(["run", f"{FIXTURE_MODULE}:greet", "Alex", "--excited"])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"], "Hello, Alex!")

    def test_human_help_for_command(self) -> None:
        code, stdout, stderr = run_cli(["--human", "run", f"{FIXTURE_MODULE}:greet", "--help"])
        self.assertEqual(code, 0, stderr)
        self.assertIn("Usage:", stdout)
        self.assertIn("--excited", stdout)
        self.assertIn("name", stdout)

    def test_structured_input_flags_override_json(self) -> None:
        code, stdout, stderr = run_cli(
            [
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
        code, stdout, stderr = run_cli(["spec", f"{FIXTURE_MODULE}:describe"])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        fields = payload["spec"]["commands"][0]["parameters"][0]["fields"]
        self.assertEqual([field["cli_name"] for field in fields], ["user.name", "user.age"])

    def test_pydantic_model_input_and_spec(self) -> None:
        code, stdout, stderr = run_cli(
            ["run", f"{FIXTURE_MODULE}:describe_profile", "--profile.name", "Alex", "--profile.age", "5"]
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"], {"age": 5, "name": "Alex"})

        code, stdout, stderr = run_cli(["spec", f"{FIXTURE_MODULE}:describe_profile"])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        fields = payload["spec"]["commands"][0]["parameters"][0]["fields"]
        self.assertEqual([field["cli_name"] for field in fields], ["profile.name", "profile.age"])

    def test_module_target_becomes_group(self) -> None:
        code, stdout, stderr = run_cli(["spec", FIXTURE_MODULE])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        names = {command["name"] for command in payload["spec"]["commands"]}
        self.assertIn("greet", names)
        self.assertIn("ops.echo", names)
        self.assertIn("ops.ping", names)

    def test_classmethod_and_staticmethod_execution(self) -> None:
        code, stdout, stderr = run_cli(["run", f"{FIXTURE_MODULE}:Ops.echo", "hello"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"], "hello")

        code, stdout, stderr = run_cli(["run", f"{FIXTURE_MODULE}:Ops.ping", "--count", "3"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"], "Ops:3")

    def test_instance_method_is_rejected(self) -> None:
        code, stdout, _ = run_cli(["run", f"{FIXTURE_MODULE}:Ops.not_supported", "3"])
        self.assertEqual(code, 1)
        payload = json.loads(stdout)
        self.assertIn("only supports functions, @staticmethod, and @classmethod", payload["error"]["message"])

    def test_unrecoverable_wrapper_error_is_clear(self) -> None:
        code, stdout, _ = run_cli(["run", f"{FIXTURE_MODULE}:opaque", "Alex"])
        self.assertEqual(code, 1)
        payload = json.loads(stdout)
        self.assertIn("preserve __wrapped__ or __signature__", payload["error"]["message"])

    def test_langchain_like_tools_work_in_both_orders(self) -> None:
        code, stdout, stderr = run_cli(["run", f"{FIXTURE_MODULE}:lang_a", "1"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"], 2)

        code, stdout, stderr = run_cli(["run", f"{FIXTURE_MODULE}:lang_b", "1"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"], 3)

    def test_alias_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"XDG_CONFIG_HOME": temp_dir}
            code, stdout, stderr = run_cli(["add", "hello", f"{FIXTURE_MODULE}:greet"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["result"]["status"], "added")

            code, stdout, stderr = run_cli(["hello", "Alex"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["result"], "Hello, Alex.")

            code, stdout, stderr = run_cli(["list"], env=env)
            self.assertEqual(code, 0, stderr)
            listing = json.loads(stdout)["result"]
            self.assertEqual(listing, [{"alias": "hello", "target": f"{FIXTURE_MODULE}:greet"}])

            code, stdout, stderr = run_cli(["remove", "hello"], env=env)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["result"]["status"], "removed")

    def test_alias_conflict_with_builtin_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            code, stdout, _ = run_cli(["add", "run", f"{FIXTURE_MODULE}:greet"], env={"XDG_CONFIG_HOME": temp_dir})
            self.assertEqual(code, 1)
            payload = json.loads(stdout)
            self.assertIn("reserved by Bashon", payload["error"]["message"])

    def test_file_target_executes(self) -> None:
        code, stdout, stderr = run_cli(["run", f"{FIXTURE_FILE}:greet", "Alex"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"], "Hello, Alex.")

    def test_annotated_param_alias_is_supported(self) -> None:
        code, stdout, stderr = run_cli(["run", f"{FIXTURE_MODULE}:nicknamed", "--person", "Alex"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["result"], "Alex")


if __name__ == "__main__":
    unittest.main()
