"""Live Playwright verifier (S4.2) — the "passing" contract, live.

The offline gate compares the patched file to the golden set's known-good
``fixed_code`` (oracle). The **live gate** actually *runs* the patched spec
against the demo app (build bible §23): this verifier applies the fixture's
``app_env`` (defect-injection flags — the app is *deliberately* wrong for
that fixture), writes the patched file as a temporary ``e2e/fix_probe.spec.js``,
runs that single spec through the demo app's own Playwright setup (its config,
``e2e/fixtures.js`` artifacts, chromium), and reports whether it passes.

Stack handling (v1, local only): the defect flags are read by the server at
**startup** and the vite dev server proxies ``/api`` to a fixed server port,
so stacks are keyed by their set of active defect flags:

- a server already answering on the server port with the **same** active
  defects is reused (e.g. a dev ``pnpm dev`` stack);
- a server with **different** active defects → the fixture fails with a
  clear error (we must not kill someone else's stack);
- no server → the verifier starts server + client itself (isolated
  ``DEMO_DB_FILE`` in a temp dir), keeps them up across same-flag fixtures,
  and stops what it started on :meth:`PlaywrightVerifier.aclose`.

The verifier is a :class:`~qa_copilot_ai.fixer.runner.FixVerifier` callable
(``await verifier(fixture, patched) -> bool``). Operational problems (stack
down, wrong defect flags, Playwright crash) surface as ``False`` plus
:attr:`last_error` — a verifier never crashes the eval run (the runner
isolates failures per fixture).

Local tooling only: no DB, no network beyond the localhost demo stack.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from qa_copilot_execution.golden import FixFixture

logger = logging.getLogger("qa_copilot_ai.fixer.live")

__all__ = ["PlaywrightVerifier", "PlaywrightVerifierError", "required_flags"]

_TRUE = {"1", "true", "yes", "on"}
_PROBE_SPEC = "e2e/fix_probe.spec.js"


class PlaywrightVerifierError(RuntimeError):
    """The demo stack could not be brought up or matched (fail loud)."""


@dataclass
class _Owned:
    """Processes this verifier started itself (and therefore may stop)."""

    flags: frozenset[str]
    server: subprocess.Popen[str] | None = None
    client: subprocess.Popen[str] | None = None
    workdir: Path | None = None


def required_flags(app_env: Mapping[str, str]) -> frozenset[str]:
    """The defect flags *app_env* asks the app to be in (true-valued keys).

    The S4.2 stack key: a server is only reusable when its active defect
    set matches, and ``{"FLAG": "0"}`` means "flag off" — so only
    true-valued entries count. The S4.3 loop derives its S3-run flags the
    same way (single source of truth for both gates).
    """
    return frozenset(name for name, value in app_env.items() if str(value).strip().lower() in _TRUE)


def _required_flags(fixture: FixFixture) -> frozenset[str]:
    """The defect flags the fixture's ``app_env`` asks the app to be in."""
    return required_flags(fixture.app_env)


def _http_ok(url: str, timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(resp.getcode()) == 200
    except (urllib.error.URLError, ConnectionError, OSError):
        return False


def _active_defects(config_url: str, timeout: float = 1.0) -> frozenset[str] | None:
    """Active defect flags of a server answering at *config_url* — None when down."""
    try:
        with urllib.request.urlopen(config_url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ConnectionError, OSError, ValueError):
        return None
    defects = payload.get("defects") if isinstance(payload, dict) else None
    if not isinstance(defects, dict):
        return None
    return frozenset(name for name, active in defects.items() if active)


def _stop_process(proc: subprocess.Popen[str] | None) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            with contextlib.suppress(subprocess.SubprocessError):
                proc.wait(timeout=5)


class PlaywrightVerifier:
    """Runs one patched spec against the demo app and returns pass/fail.

    A :class:`~qa_copilot_ai.fixer.runner.FixVerifier` callable —
    ``await verifier(fixture, patched) -> bool`` — plus
    :meth:`aclose` for the stack it started (callers own the lifecycle).
    """

    def __init__(
        self,
        demo_app_dir: Path | str,
        *,
        server_port: int = 4000,
        client_port: int = 5174,
        node: str = "node",
        start_timeout_s: float = 60.0,
        test_timeout_s: float = 180.0,
    ) -> None:
        self._demo_app = Path(demo_app_dir)
        self._server_port = server_port
        self._client_port = client_port
        self._node = node
        self._start_timeout_s = start_timeout_s
        self._test_timeout_s = test_timeout_s
        self._owned: _Owned | None = None
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        """Why the most recent call returned ``False`` (None when passing)."""
        return self._last_error

    @property
    def _client_url(self) -> str:
        return f"http://127.0.0.1:{self._client_port}"

    @property
    def _server_health(self) -> str:
        return f"http://127.0.0.1:{self._server_port}/health"

    @property
    def _server_config(self) -> str:
        return f"http://127.0.0.1:{self._server_port}/api/config"

    # ------------------------------------------------------------------
    # FixVerifier contract
    # ------------------------------------------------------------------

    async def __call__(self, fixture: FixFixture, patched: str) -> bool:
        """Apply *patched* as a probe spec and run it against the demo app."""
        required = _required_flags(fixture)
        try:
            ok, detail = await self.run_spec(patched, spec_name=_PROBE_SPEC, flags=required)
        except PlaywrightVerifierError as exc:
            self._fail(f"{fixture.id}: {exc}")
            return False
        if not ok:
            self._fail(f"{fixture.id}: {detail}")
            return False
        self._last_error = None
        return True

    async def run_spec(
        self,
        spec_text: str,
        *,
        spec_name: str = _PROBE_SPEC,
        flags: frozenset[str] = frozenset(),
    ) -> tuple[bool, str]:
        """Write *spec_text* to *spec_name* and run it against the demo app.

        The reusable S3 primitive behind ``__call__`` — also the S4.3
        Approve → re-run loop's spec executor (``qa_copilot_ai.loop.live``
        runs the broken spec, then the patched spec, through this method).
        *flags* are the demo-app defect switches active for this run; the
        stack is started with them (the app is stateless, so the state
        holds for the session).

        Returns ``(ok, detail)`` — *ok* is the pass/fail verdict
        (playwright exit 0), *detail* the raw output.
        """
        self._check_spec_name(spec_name)
        probe = self._demo_app / spec_name
        try:
            await asyncio.to_thread(self._ensure_stack, flags)
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text(spec_text, encoding="utf-8")
            return await asyncio.to_thread(self._run_spec, spec_name)
        finally:
            with contextlib.suppress(OSError):
                probe.unlink()

    def _check_spec_name(self, spec_name: str) -> None:
        """Spec names are confined to the demo app root (no ``..`` escape)."""
        if not spec_name or spec_name.startswith(("/", "\\")) or ".." in Path(spec_name).parts:
            raise ValueError(f"unsafe spec_name: {spec_name!r}")

    async def aclose(self) -> None:
        """Stop the demo stack this verifier started (no-op otherwise)."""
        await asyncio.to_thread(self._release_owned)
        self._last_error = None

    # ------------------------------------------------------------------
    # stack management (synchronous — call via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _fail(self, message: str) -> None:
        self._last_error = message
        logger.warning("verifier: %s", message)

    def _ensure_stack(self, required: frozenset[str]) -> None:
        """Guarantee a demo stack with exactly *required* defects active."""
        if self._owned is not None:
            if self._owned.flags == required:
                self._ensure_client_up()
                return
            self._release_owned()  # our own stack, wrong flags → restart
        active = _active_defects(self._server_config)
        if active is not None:
            if active != required:
                raise PlaywrightVerifierError(
                    f"demo server on :{self._server_port} has active defects "
                    f"{sorted(active) or 'none'} but the fixture requires "
                    f"{sorted(required) or 'none'} — stop that stack and rerun the gate"
                )
            self._ensure_client_up()
            return
        self._start_stack(required)

    def _ensure_client_up(self) -> None:
        if _http_ok(self._client_url, timeout=1.0):
            return
        self._start_client()

    def _start_stack(self, required: frozenset[str]) -> None:
        """Start server (fresh isolated DB + required defects) and client."""
        workdir = Path(tempfile.mkdtemp(prefix="qa_fixer_probe_"))
        owned = _Owned(flags=required, workdir=workdir)
        server_log = open(workdir / "server.log", "ab")
        server_env = dict(os.environ)
        server_env["PORT"] = str(self._server_port)
        server_env["DEMO_DB_FILE"] = str(workdir / "demo.sqlite")
        for name in required:
            server_env[name] = "1"
        try:
            owned.server = subprocess.Popen(
                [self._node, "server/src/index.js"],
                cwd=self._demo_app,
                env=server_env,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                text=True,
                **_popen_kwargs(),
            )
            self._wait_for(
                lambda: _http_ok(self._server_health),
                what=f"demo server on :{self._server_port}",
                proc=owned.server,
            )
            self._start_client(owned=owned)
        except PlaywrightVerifierError:
            server_log.close()
            self._discard(owned)
            raise
        server_log.close()
        self._owned = owned
        logger.info("verifier: started demo stack (defects=%s)", sorted(required) or "none")

    def _start_client(self, *, owned: _Owned | None = None) -> None:
        target = owned if owned is not None else self._owned
        if target is not None:
            target.client = self._spawn_client()
            self._wait_for(
                lambda: _http_ok(self._client_url),
                what=f"vite client on :{self._client_port}",
                proc=target.client,
            )
            return
        # No owned stack (external server matched, client down): own the client.
        self._owned = _Owned(flags=_active_defects(self._server_config) or frozenset())
        self._start_client(owned=self._owned)

    def _spawn_client(self) -> subprocess.Popen[str]:
        # ``cwd`` is already ``<demo app>/client`` — resolve the vite entry
        # relative to it (a ``client/`` prefix would double the segment).
        # Bind the IPv4 loopback explicitly: on this machine vite's default
        # ``localhost`` binds ``::1`` only, but the health check and
        # ``APP_UNDER_TEST`` use ``127.0.0.1``.
        return subprocess.Popen(
            [
                self._node,
                "node_modules/vite/bin/vite.js",
                "--host",
                "127.0.0.1",
                "--port",
                str(self._client_port),
                "--strictPort",
            ],
            cwd=self._demo_app / "client",
            env=dict(os.environ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            text=True,
            **_popen_kwargs(),
        )

    def _wait_for(
        self,
        ready: Callable[[], bool],
        *,
        what: str,
        proc: subprocess.Popen[str] | None = None,
    ) -> None:
        deadline = time.monotonic() + self._start_timeout_s
        while not ready():
            if proc is not None and proc.poll() is not None:
                raise PlaywrightVerifierError(
                    f"{what} exited early (exit {proc.returncode}); "
                    f"log: {self._owned.workdir if self._owned else 'n/a'}"
                )
            if time.monotonic() > deadline:
                raise PlaywrightVerifierError(
                    f"{what} did not become ready within {self._start_timeout_s:g}s"
                )
            time.sleep(0.5)

    def _release_owned(self) -> None:
        owned = self._owned
        if owned is None:
            return
        self._owned = None
        self._discard(owned)
        logger.info("verifier: stopped owned demo stack")

    def _discard(self, owned: _Owned) -> None:
        _stop_process(owned.client)
        _stop_process(owned.server)
        if owned.workdir is not None:
            with contextlib.suppress(OSError):
                shutil.rmtree(owned.workdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # spec execution
    # ------------------------------------------------------------------

    def _run_spec(self, spec_name: str = _PROBE_SPEC) -> tuple[bool, str]:
        """Run the probe spec in the demo app; returns (ok, detail)."""
        playwright_cli = self._demo_app / "node_modules/@playwright/test/cli.js"
        if not playwright_cli.is_file():
            return False, f"Playwright CLI not installed at {playwright_cli}"
        env = dict(os.environ)
        env["APP_UNDER_TEST"] = self._client_url
        env["CI"] = "1"
        try:
            proc = subprocess.run(
                [
                    self._node,
                    str(playwright_cli),
                    "test",
                    spec_name,
                    "--reporter=json",
                    "--forbid-only",
                    "--workers=1",
                ],
                cwd=self._demo_app,
                env=env,
                capture_output=True,
                text=True,
                timeout=self._test_timeout_s,
            )
        except subprocess.TimeoutExpired:
            return False, f"Playwright run timed out after {self._test_timeout_s:g}s"

        report = _parse_report(proc.stdout)
        if report is None:
            tail = (proc.stdout + "\n" + (proc.stderr or "")).strip()[-800:]
            return False, f"no usable Playwright JSON report (exit {proc.returncode}): {tail}"
        specs = _probe_specs(report, spec_name)
        if not specs:
            return False, "Playwright report contains no probe spec results"
        failed = [str(spec.get("title") or spec) for spec in specs if spec.get("ok") is not True]
        if failed:
            return False, f"probe spec did not pass: {failed[0]}{_first_failure_detail(report)}"
        return True, "probe spec passed"


class _PopenKwargs(TypedDict, total=False):
    """Platform-specific optional ``Popen`` keyword arguments."""

    creationflags: int


def _popen_kwargs() -> _PopenKwargs:
    """Keep subprocesses from popping a console window on Windows."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _parse_report(stdout: str) -> dict[str, object] | None:
    try:
        payload = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(payload, dict) or "suites" not in payload:
        return None
    return payload


def _probe_specs(
    report: dict[str, object], spec_name: str = _PROBE_SPEC
) -> list[dict[str, object]]:
    """Spec results for the probe file.

    The JSON reporter reports suite ``file`` paths **relative to
    ``testDir``** (``./e2e`` in the demo app), so the probe shows up as
    ``fix_probe.spec.js``, not ``e2e/fix_probe.spec.js`` — match on the
    basename. Only the probe file is ever passed to the CLI, so this
    cannot pick up another spec's results.
    """
    base = spec_name.replace("\\", "/").rsplit("/", 1)[-1]
    found: list[dict[str, object]] = []

    def walk(node: object) -> None:
        if not isinstance(node, dict):
            return
        file = str(node.get("file") or "").replace("\\", "/")
        if file.endswith(base):
            specs = node.get("specs")
            if isinstance(specs, list):
                found.extend(spec for spec in specs if isinstance(spec, dict))
        child_suites = node.get("suites")
        if isinstance(child_suites, list):
            for child in child_suites:
                walk(child)

    suites = report.get("suites")
    if isinstance(suites, list):
        for suite in suites:
            walk(suite)
    return found


def _first_failure_detail(report: dict[str, object]) -> str:
    """Best-effort one-liner from the report's error field (for diagnostics)."""

    def walk(node: object) -> str | None:
        if not isinstance(node, dict):
            return None
        specs = node.get("specs")
        if isinstance(specs, list):
            for spec in specs:
                if not isinstance(spec, dict) or spec.get("ok") is True:
                    continue
                tests = spec.get("tests")
                if not isinstance(tests, list):
                    continue
                for test in tests:
                    if not isinstance(test, dict):
                        continue
                    results = test.get("results")
                    if not isinstance(results, list):
                        continue
                    for attempt in results:
                        if isinstance(attempt, dict) and attempt.get("error"):
                            return f" — {str(attempt['error']).strip()[:200]}"
        child_suites = node.get("suites")
        if isinstance(child_suites, list):
            for child in child_suites:
                found = walk(child)
                if found is not None:
                    return found
        return None

    return walk(report) or ""
