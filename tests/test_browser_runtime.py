from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import browser_runtime as runtime


class BrowserRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(dir=runtime.ROOT / ".tmp")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    @unittest.skipUnless(os.name == "nt", "Windows creation flags")
    def test_hidden_options_suppress_console_at_creation(self) -> None:
        options = runtime.hidden_process_options()
        self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertTrue(
            options["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW
        )
        self.assertEqual(options["startupinfo"].wShowWindow, subprocess.SW_HIDE)

    @unittest.skipUnless(os.name == "nt", "Windows npm native launcher")
    def test_npm_shim_resolves_native_binary_without_running_wrapper(self) -> None:
        shim = self.root / "agent-browser.cmd"
        native = self.root / "node_modules/agent-browser/bin/agent-browser-win32-x64.exe"
        native.parent.mkdir(parents=True)
        native.write_bytes(b"fixture")
        with mock.patch.object(runtime.shutil, "which", return_value=str(shim)):
            self.assertEqual(runtime.resolve_agent_browser({}), native)

    @unittest.skipUnless(os.name == "nt", "Windows mise native launcher")
    def test_mise_shim_uses_active_node_install_not_shim(self) -> None:
        shim = self.root / "mise/shims/agent-browser.exe"
        node = self.root / "mise/installs/node/24.1.0"
        native = node / "node_modules/agent-browser/bin/agent-browser-win32-x64.exe"
        native.parent.mkdir(parents=True)
        native.write_bytes(b"fixture")
        with (
            mock.patch.object(
                runtime.shutil, "which", side_effect=[str(shim), "mise.exe"]
            ),
            mock.patch.object(
                runtime, "run_hidden",
                return_value=subprocess.CompletedProcess([], 0, str(node), ""),
            ) as runner,
        ):
            self.assertEqual(runtime.resolve_agent_browser({}), native)
        self.assertEqual(runner.call_args.args[0], ["mise.exe", "where", "node"])

    def session(self) -> runtime.AgentBrowserSession:
        with mock.patch.object(runtime, "resolve_agent_browser", return_value=Path("native.exe")):
            return runtime.AgentBrowserSession("test-namespace", "test-session", root=self.root)

    def test_commands_keep_session_and_close_does_not_launch_or_require_live_cdp(self) -> None:
        session = self.session()
        session.cdp = "ws://127.0.0.1:12345/devtools/browser/owned"
        completed = subprocess.CompletedProcess([], 0, "", "")
        with (
            mock.patch.object(session, "start"),
            mock.patch.object(session, "reap_browser") as reap,
            mock.patch.object(runtime, "run_hidden", return_value=completed) as runner,
        ):
            session.run(["open", "about:blank"], capture=False)
            session.run(["eval", "1+1"])
            session.run(["close"], capture=False)
        for call in runner.call_args_list:
            command = call.args[0]
            self.assertEqual(command[1:5], ["--namespace", "test-namespace", "--session", "test-session"])
            if command[-1] != "close":
                self.assertEqual(command[5:7], ["--cdp", session.cdp])
            else:
                self.assertIn("--config", command)
                self.assertNotIn("--cdp", command)
                self.assertNotIn("AGENT_BROWSER_ALLOW_FILE_ACCESS", call.kwargs["env"])
                self.assertNotIn("AGENT_BROWSER_ARGS", call.kwargs["env"])
        first = runner.call_args_list[0].kwargs
        self.assertEqual(first["stdout"], subprocess.DEVNULL)
        self.assertNotIn("capture_output", first)
        reap.assert_called_once()

    def test_close_without_start_never_launches_a_new_daemon(self) -> None:
        session = self.session()
        with mock.patch.object(runtime, "run_hidden") as runner:
            result = session.run(["close"])
        self.assertEqual(result.returncode, 0)
        runner.assert_not_called()

    def test_failed_close_retains_exact_session_for_retry(self) -> None:
        key = ("failed-close", "owned")
        instance = mock.Mock()
        instance.run.side_effect = [
            subprocess.CompletedProcess([], 1, "", "failure"),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        with mock.patch.dict(runtime._SESSIONS, {key: instance}, clear=True):
            self.assertEqual(runtime.run_agent(*key, ["close"]).returncode, 1)
            self.assertIs(runtime._SESSIONS[key], instance)
            self.assertEqual(runtime.run_agent(*key, ["close"]).returncode, 0)
            self.assertNotIn(key, runtime._SESSIONS)

    def test_close_reaps_owned_child_even_when_cli_times_out(self) -> None:
        session = self.session()
        session.invoked = True
        chrome = session.chrome = mock.Mock()
        chrome.poll.return_value = None
        with mock.patch.object(
            runtime, "run_hidden", side_effect=subprocess.TimeoutExpired([], 1)
        ), self.assertRaises(subprocess.TimeoutExpired):
            session.run(["close"])
        chrome.terminate.assert_called_once()
        chrome.wait.assert_called_once_with(timeout=15)
        self.assertIsNone(session.chrome)

    @unittest.skipUnless(os.name == "nt", "Windows managed Chromium")
    def test_chrome_spawn_is_hidden_and_profile_is_project_local(self) -> None:
        session = self.session()
        chrome = mock.Mock()
        chrome.poll.return_value = None
        with (
            mock.patch.object(runtime, "resolve_chromium", return_value=Path("chrome.exe")),
            mock.patch.object(runtime.subprocess, "Popen", return_value=chrome) as popen,
            mock.patch.object(Path, "read_text", return_value="12345\n/devtools/browser/test\n"),
        ):
            session.start()
        command = popen.call_args.args[0]
        self.assertIn("--headless=new", command)
        self.assertIn(f"--user-data-dir={session.profile}", command)
        self.assertTrue(session.profile.is_relative_to(self.root / ".tmp"))
        self.assertEqual(popen.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertEqual(session.cdp, "ws://127.0.0.1:12345/devtools/browser/test")

    @unittest.skipUnless(os.name == "nt", "Windows startup timeout")
    def test_startup_timeout_close_reaps_profile_and_preserves_evidence_log(self) -> None:
        evidence = self.root / "evidence"
        with mock.patch.object(runtime, "resolve_agent_browser", return_value=Path("native.exe")):
            session = runtime.AgentBrowserSession("timeout", "owned", root=self.root, scratch=evidence)
        chrome = mock.Mock()
        chrome.poll.return_value = None
        with (
            mock.patch.object(runtime, "resolve_chromium", return_value=Path("chrome.exe")),
            mock.patch.object(runtime.subprocess, "Popen", return_value=chrome),
            mock.patch.object(runtime, "run_hidden") as cli,
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                session.start(timeout=0)
            profile = session.profile
            self.assertEqual(session.run(["close"]).returncode, 0)
        cli.assert_not_called()
        chrome.terminate.assert_called_once()
        self.assertFalse(profile.exists())
        self.assertTrue((evidence / "browser.log").is_file())


if __name__ == "__main__":
    unittest.main()
