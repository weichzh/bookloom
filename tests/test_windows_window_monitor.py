from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import windows_window_monitor as monitor


class WindowMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        (monitor.ROOT / ".tmp").mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=monitor.ROOT / ".tmp")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / ".tmp").mkdir()

    def test_evidence_accepts_resolved_paths_only_inside_repository_tmp(self) -> None:
        wanted = self.root / ".tmp" / "run" / "events.json"
        self.assertEqual(monitor.evidence_path(".tmp/run/events.json", root=self.root), wanted)
        self.assertEqual(monitor.evidence_path(wanted, root=self.root), wanted)
        for path in (".tmp", "Works/events.json", ".tmp/../Works/events.json", self.root.parent / "events.json"):
            with self.subTest(path=str(path)), self.assertRaises(monitor.MonitorError):
                monitor.evidence_path(path, root=self.root)

    def test_pid_reuse_cannot_adopt_an_old_terminal_into_command_tree(self) -> None:
        inventory = monitor.ProcessInventory()
        inventory.observe([(100, 0, "old-parent.exe"), (200, 100, "conhost.exe")], 0)
        old_parent = inventory.current[200]["parent_instance"]
        command = inventory.mark_command(100, "uv.exe", 1)
        inventory.observe([
            (100, 9, "uv.exe"), (200, 100, "conhost.exe"),
            (201, 100, "chrome.exe"), (202, 201, "conhost.exe"),
        ], 1.1)
        self.assertNotEqual(old_parent, command)
        events = [
            {"pid": pid, "at": at, "class": kind, "event": event, "visible": visible}
            for pid, at, kind, event, visible in (
                (200, 0.01, "ConsoleWindowClass", monitor.EVENT_OBJECT_SHOW, True),
                (202, 1.1, "PseudoConsoleWindow", monitor.EVENT_OBJECT_SHOW, True),
                (999, 1.1, "CASCADIA_HOSTING_WINDOW_CLASS", monitor.EVENT_SYSTEM_FOREGROUND, False),
                (201, 1.1, "Chrome_WidgetWin_1", monitor.EVENT_OBJECT_CREATE, False),
            )
        ]
        result = monitor.classify_events(events, inventory.records, command)
        self.assertEqual(
            [event["attribution"] for event in result],
            ["other_processes", "command_tree", "unattributed", "command_tree"],
        )
        summary = monitor.event_summary(result, monitor_ok=True)
        self.assertEqual(summary["command_tree_terminal_events"], 1)
        self.assertEqual(summary["other_processes_terminal_events"], 1)
        self.assertEqual(summary["unattributed_terminal_events"], 1)
        self.assertEqual(summary["command_tree_visible_terminal_shows"], 1)
        self.assertEqual(summary["unattributed_visible_terminal_shows"], 0)

    def test_command_mark_reuses_a_toolhelp_sample_that_won_spawn_race(self) -> None:
        inventory = monitor.ProcessInventory()
        inventory.observe([(100, 9, "uv.exe"), (101, 100, "python.exe")], 1.01)
        command = inventory.mark_command(100, "uv.exe", 1)
        self.assertEqual(inventory.current[101]["parent_instance"], command)

    def test_pre_spawn_snapshot_cannot_drop_retained_command_identity(self) -> None:
        inventory = monitor.ProcessInventory()
        command = inventory.mark_command(100, "uv.exe", 1)
        inventory.observe([], 1.001)
        inventory.observe([(100, 9, "uv.exe"), (101, 100, "python.exe")], 1.02)
        self.assertEqual(inventory.current[100]["instance"], command)
        self.assertEqual(inventory.current[101]["parent_instance"], command)

    def test_confirmed_root_exit_cannot_adopt_reused_pid_or_its_child(self) -> None:
        inventory = monitor.ProcessInventory()
        command = inventory.mark_command(100, "uv.exe", 1)
        inventory.observe([(100, 9, "uv.exe")], 1.01)
        inventory.observe([], 1.02)
        inventory.observe([(100, 9, "notepad.exe"), (101, 100, "conhost.exe")], 1.03)
        self.assertNotEqual(inventory.current[100]["instance"], command)
        self.assertNotIn(100, inventory.command_roots)
        self.assertEqual(inventory.records[command - 1]["retired_at"], 1.02)
        events = [
            {"pid": pid, "at": 1.03, "class": "ConsoleWindowClass",
             "event": monitor.EVENT_OBJECT_SHOW, "visible": True}
            for pid in (100, 101)
        ]
        attributed = monitor.classify_events(events, inventory.records, command)
        self.assertTrue(all(event["attribution"] != "command_tree" for event in attributed))

    def test_retirement_preserves_existing_descendant_history(self) -> None:
        inventory = monitor.ProcessInventory()
        command = inventory.mark_command(100, "uv.exe", 1)
        inventory.observe([(100, 9, "uv.exe"), (101, 100, "chrome.exe")], 1.01)
        inventory.observe([(101, 100, "chrome.exe")], 1.02)
        self.assertEqual(inventory.current[101]["parent_instance"], command)
        event = {"pid": 101, "at": 1.02, "class": "PseudoConsoleWindow",
                 "event": monitor.EVENT_OBJECT_SHOW, "visible": True}
        self.assertEqual(
            monitor.classify_events([event], inventory.records, command)[0]["attribution"],
            "command_tree",
        )

    def test_popen_exit_retires_root_even_if_toolhelp_never_sampled_it(self) -> None:
        inventory = monitor.ProcessInventory()
        command = inventory.mark_command(100, "uv.exe", 1)
        inventory.observe([], 1.001)
        inventory.retire_command(100, 1.002)
        inventory.observe([(100, 9, "notepad.exe"), (101, 100, "conhost.exe")], 1.02)
        self.assertNotEqual(inventory.current[100]["instance"], command)
        self.assertNotEqual(inventory.current[101]["parent_instance"], command)

    def test_failed_hook_unhooks_only_prior_successful_hook_and_reports_failure(self) -> None:
        user, kernel = mock.Mock(), mock.Mock()
        user.SetWinEventHook.side_effect = [123, 0]
        user.UnhookWinEvent.return_value = True
        instance = monitor.WindowMonitor(api=(user, kernel, lambda callback: callback))
        instance._hook_loop()
        self.assertEqual(user.SetWinEventHook.call_count, 2)
        user.UnhookWinEvent.assert_called_once_with(123)
        self.assertTrue(instance.hook_ready.is_set())
        self.assertIn("SetWinEventHook", instance.errors[0])
        summary = instance.record()["summary"]
        self.assertFalse(summary["monitor_ok"])
        self.assertIsNone(summary["command_tree_terminal_events"])
        self.assertIsNone(summary["other_processes_visible_terminal_shows"])

    def test_first_failed_hook_does_not_unhook_foreign_handles(self) -> None:
        user, kernel = mock.Mock(), mock.Mock()
        user.SetWinEventHook.return_value = 0
        instance = monitor.WindowMonitor(api=(user, kernel, lambda callback: callback))
        instance._hook_loop()
        user.UnhookWinEvent.assert_not_called()
        self.assertTrue(instance.errors)

    def test_hook_failure_prevents_command_launch_and_writes_failed_evidence(self) -> None:
        output = self.root / ".tmp" / "events.json"
        instance = mock.Mock()
        instance.start.side_effect = monitor.MonitorError("SetWinEventHook failed")
        instance.record.return_value = {
            "summary": monitor.event_summary([], monitor_ok=False),
            "monitor_errors": [], "events": [], "processes": [],
        }
        with (
            mock.patch.object(monitor, "WindowMonitor", return_value=instance),
            mock.patch.object(monitor, "ROOT", self.root),
            mock.patch.object(monitor, "evidence_path", return_value=output),
            mock.patch.object(monitor.subprocess, "Popen") as popen,
            mock.patch("sys.stderr"),
        ):
            code = monitor.main(["--out", str(output), "--", "uv", "run", "probe"])
        popen.assert_not_called()
        self.assertEqual(code, 2)
        evidence = json.loads(output.read_text(encoding="utf-8"))
        self.assertIsNone(evidence["command_returncode"])
        self.assertIsNone(evidence["summary"]["command_tree_terminal_events"])
        self.assertIn("SetWinEventHook failed", evidence["monitor_errors"])

    def test_command_argv_is_hidden_without_shell_and_exit_code_is_preserved(self) -> None:
        output = self.root / ".tmp" / "events.json"
        instance = mock.Mock()
        instance.started = 0
        instance.record.return_value = {
            "summary": monitor.event_summary([], monitor_ok=True),
            "monitor_errors": [], "events": [], "processes": [],
        }
        child = mock.Mock(pid=123)
        child.wait.return_value = 7
        command = ["tool.exe", "an argument with spaces", "a&b"]
        with (
            mock.patch.object(monitor, "WindowMonitor", return_value=instance),
            mock.patch.object(monitor, "evidence_path", return_value=output),
            mock.patch.object(monitor.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            mock.patch.object(monitor.subprocess, "Popen", return_value=child) as popen,
            mock.patch.object(monitor.time, "sleep"),
            mock.patch("sys.stderr"),
        ):
            code = monitor.main(["--out", str(output), "--", *command])
        self.assertEqual(code, 7)
        self.assertEqual(popen.call_args.args[0], command)
        self.assertEqual(popen.call_args.kwargs["creationflags"], 0x08000000)
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertEqual(popen.call_args.kwargs["stdin"], monitor.subprocess.DEVNULL)
        instance.stop.assert_called_once()
        instance.command_exited.assert_called_once_with(child.pid)


if __name__ == "__main__":
    unittest.main()
