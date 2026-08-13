"""
Lightweight Python code execution for the Telegram Ollama bot.

This module provides a practical execution mechanism for AI-generated Python
code. It is **not a secure sandbox**. The subprocess inherits the bot process's
permissions and environment. Known limitations:

  - Same-user privilege as the bot on the host filesystem/network.
  - No memory/CPU quota; runaway code can exhaust resources.
  - Network access is not restricted by this layer.
  - The workspace boundary is a filesystem convention, not a mandatory
    security boundary against a determined attacker.
  - Without a virtualenv, pip packages are installed into a shared eval
    directory and exposed via PYTHONPATH, not into the main bot environment,
    but they are still visible to any eval subprocess.

Use it as a convenience feature for trusted users, not as a multi-tenant
execution platform.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional


class EvalError(Exception):
    """Raised when execution cannot proceed."""


class PythonEval:
    def __init__(
        self,
        workspace: "WorkspaceManager",
        env_dir: str = "eval_env",
        timeout: int = 30,
        max_stdout_bytes: int = 256 * 1024,
        max_stderr_bytes: int = 256 * 1024,
    ) -> None:
        self.workspace = workspace
        self.env_dir = Path(env_dir)
        self.timeout = timeout
        self.max_stdout_bytes = max_stdout_bytes
        self.max_stderr_bytes = max_stderr_bytes
        self._python: Optional[Path] = None
        self._pip: Optional[Path] = None
        self._install_lock = False
        self._use_venv = False
        self._site_packages: Optional[Path] = None

    def ensure_env(self) -> None:
        if self._python and self._python.exists():
            return
        py = self.env_dir / "bin" / "python"
        if py.exists():
            self._python = py
            self._pip = self.env_dir / "bin" / "pip"
            self._use_venv = True
            return
        # Try to create a venv. If that fails, fall back to system Python with
        # a shared packages directory so pip installs do not touch the main env.
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", str(self.env_dir)],
                check=True,
                capture_output=True,
            )
            self._python = self.env_dir / "bin" / "python"
            self._pip = self.env_dir / "bin" / "pip"
            self._use_venv = True
        except Exception:
            self._python = Path(sys.executable)
            self._pip = Path(sys.executable).parent / "pip"
            if not self._pip.exists():
                self._pip = Path(sys.executable)
            self._use_venv = False
            pkgs = self.env_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
            pkgs.mkdir(parents=True, exist_ok=True)
            self._site_packages = pkgs

    @property
    def python_bin(self) -> Path:
        self.ensure_env()
        return self._python  # type: ignore[return-value]

    @property
    def pip_bin(self) -> Path:
        self.ensure_env()
        return self._pip  # type: ignore[return-value]

    def _env(self) -> dict:
        env = os.environ.copy()
        if not self._use_venv and self._site_packages:
            key = "PYTHONPATH"
            prev = env.get(key, "")
            env[key] = str(self._site_packages) + (os.pathsep + prev if prev else "")
        return env

    def _pip_install_cmd(self, pkg: str) -> list[str]:
        base = [str(self.pip_bin), "install", "--disable-pip-version-check", "--no-cache-dir", pkg]
        if not self._use_venv:
            return [str(self.python_bin), "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--target", str(self._site_packages), pkg]
        return base

    async def execute(self, user_id: int, code: str, install: Optional[list[str]] = None) -> dict:
        if self._install_lock:
            return {
                "success": False,
                "stdout": "",
                "stderr": "Package installation already in progress. Try again shortly.",
                "exit_code": -1,
                "files_created": [],
                "execution_time": 0.0,
            }
        if not code.strip():
            return {
                "success": False,
                "stdout": "",
                "stderr": "Empty code block.",
                "exit_code": -1,
                "files_created": [],
                "execution_time": 0.0,
            }

        # Syntax check before execution.
        try:
            ast.parse(code)
        except SyntaxError as exc:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"SyntaxError: {exc}",
                "exit_code": -1,
                "files_created": [],
                "execution_time": 0.0,
            }

        # Install requested packages in the eval env (not the bot env).
        if install:
            self._install_lock = True
            try:
                for pkg in install:
                    pkg = pkg.strip()
                    if not pkg:
                        continue
                    cmd = self._pip_install_cmd(pkg)
                    if not self._use_venv:
                        cmd.extend(["--target", str(self._site_packages)])
                    proc = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout,
                        env=self._env(),
                    )
                    if proc.returncode != 0:
                        return {
                            "success": False,
                            "stdout": "",
                            "stderr": f"pip install {pkg} failed:\n{proc.stderr[:self.max_stderr_bytes]}",
                            "exit_code": proc.returncode,
                            "files_created": [],
                            "execution_time": 0.0,
                        }
            finally:
                self._install_lock = False

        user_dir = self.workspace._user_dir(user_id)
        before_files = set(self.workspace._iter_files(user_dir))
        start = time.perf_counter()
        script = None
        try:
            fd, script = tempfile.mkstemp(suffix=".py", prefix="eval_")
            try:
                os.write(fd, code.encode("utf-8"))
            finally:
                os.close(fd)
            proc = subprocess.run(
                [str(self.python_bin), script],
                cwd=str(user_dir),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self._env(),
            )
            elapsed = time.perf_counter() - start
            after_files = set(self.workspace._iter_files(user_dir))
            created = sorted(str(p.relative_to(user_dir)) for p in (after_files - before_files))
            success = proc.returncode == 0
            return {
                "success": success,
                "stdout": proc.stdout[: self.max_stdout_bytes],
                "stderr": proc.stderr[: self.max_stderr_bytes],
                "exit_code": proc.returncode,
                "files_created": created,
                "execution_time": round(elapsed, 3),
            }
        except subprocess.TimeoutExpired:
            elapsed = time.perf_counter() - start
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Execution timed out after {self.timeout}s.",
                "exit_code": -1,
                "files_created": [],
                "execution_time": round(elapsed, 3),
            }
        except Exception as exc:
            elapsed = time.perf_counter() - start
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Execution error: {exc}",
                "exit_code": -1,
                "files_created": [],
                "execution_time": round(elapsed, 3),
            }
        finally:
            if script and os.path.exists(script):
                try:
                    os.unlink(script)
                except Exception:
                    pass
