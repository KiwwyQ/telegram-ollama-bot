import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

from eval_tool import PythonEval, EvalError


def _make_workspace(tmpdir):
    class FakeWS:
        def __init__(self, root):
            self.root = Path(root)
            self.root.mkdir(parents=True, exist_ok=True)

        def _user_dir(self, user_id):
            d = self.root / str(user_id)
            d.mkdir(parents=True, exist_ok=True)
            return d

        def _iter_files(self, user_dir):
            return {f for f in user_dir.rglob("*") if f.is_file()}

    return FakeWS(tmpdir)


class EvalBasicTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.ws = _make_workspace(self.tmpdir.name)
        self.eval = PythonEval(
            workspace=self.ws,
            env_dir=os.path.join(self.tmpdir.name, "eval_env"),
            timeout=5,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_simple_print(self):
        result = asyncio.get_event_loop().run_until_complete(
            self.eval.execute(1, "print('hello')")
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["stdout"], "hello\n")
        self.assertEqual(result["exit_code"], 0)

    def test_calculation(self):
        result = asyncio.get_event_loop().run_until_complete(
            self.eval.execute(1, "print(2 + 2)")
        )
        self.assertTrue(result["success"])
        self.assertIn("4", result["stdout"])

    def test_syntax_error(self):
        result = asyncio.get_event_loop().run_until_complete(
            self.eval.execute(1, "print('hello'")
        )
        self.assertFalse(result["success"])
        self.assertIn("SyntaxError", result["stderr"])

    def test_empty_code(self):
        result = asyncio.get_event_loop().run_until_complete(
            self.eval.execute(1, "   ")
        )
        self.assertFalse(result["success"])
        self.assertIn("Empty code", result["stderr"])

    def test_write_file_in_workspace(self):
        code = "open('out.txt', 'w').write('hi')\nprint('done')"
        result = asyncio.get_event_loop().run_until_complete(
            self.eval.execute(1, code)
        )
        self.assertTrue(result["success"])
        self.assertIn("out.txt", result["files_created"])
        content = (self.ws._user_dir(1) / "out.txt").read_text(encoding="utf-8")
        self.assertEqual(content, "hi")

    def test_read_file_in_workspace(self):
        (self.ws._user_dir(1) / "in.txt").write_text("world", encoding="utf-8")
        code = "print(open('in.txt').read())"
        result = asyncio.get_event_loop().run_until_complete(
            self.eval.execute(1, code)
        )
        self.assertTrue(result["success"])
        self.assertIn("world", result["stdout"])

    def test_stdout_stderr_separated(self):
        code = "import sys\nprint('out')\nsys.stderr.write('err\\n')"
        result = asyncio.get_event_loop().run_until_complete(
            self.eval.execute(1, code)
        )
        self.assertTrue(result["success"])
        self.assertIn("out", result["stdout"])
        self.assertIn("err", result["stderr"])

    def test_timeout(self):
        self.eval.timeout = 1
        result = asyncio.get_event_loop().run_until_complete(
            self.eval.execute(1, "import time; time.sleep(5)")
        )
        self.assertFalse(result["success"])
        self.assertIn("timed out", result["stderr"])

    def test_exit_code_nonzero(self):
        result = asyncio.get_event_loop().run_until_complete(
            self.eval.execute(1, "import sys; sys.exit(42)")
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], 42)

    def test_output_truncation(self):
        big = "x" * (self.eval.max_stdout_bytes + 100)
        code = f"print('{big}')"
        result = asyncio.get_event_loop().run_until_complete(
            self.eval.execute(1, code)
        )
        self.assertTrue(result["success"])
        self.assertLessEqual(len(result["stdout"]), self.eval.max_stdout_bytes)

    def test_workspace_is_cwd(self):
        code = "import os; print(os.getcwd())"
        result = asyncio.get_event_loop().run_until_complete(
            self.eval.execute(1, code)
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["stdout"].strip(), str(self.ws._user_dir(1)))

    def test_require_comment_parsed(self):
        # In this sandbox pip install may fail (no network). The important thing
        # is that the REQUIRE marker triggers a pip install attempt.
        code = "# REQUIRE: this-package-definitely-does-not-exist\nimport this_package"
        result = asyncio.get_event_loop().run_until_complete(
            self.eval.execute(1, code, install=["this-package-definitely-does-not-exist"])
        )
        self.assertFalse(result["success"])
        self.assertIn("pip install", result["stderr"])


if __name__ == "__main__":
    unittest.main()
