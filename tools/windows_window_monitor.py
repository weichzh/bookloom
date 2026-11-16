#!/usr/bin/env python3
"""Record Windows window events around one hidden argv command.

This diagnostic wrapper does not close windows or terminate process trees. It
records WinEvent notifications and observed Toolhelp ancestry, then unhooks only
its own listeners. Unattributed terminal events remain separate from events in
the command's observed process tree.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVENT_SYSTEM_FOREGROUND = 3
EVENT_OBJECT_CREATE = 0x8000
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_HIDE = 0x8003
POLL_INTERVAL = 0.008


class MonitorError(RuntimeError):
    pass


def evidence_path(value: str | Path, *, root: Path = ROOT) -> Path:
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    temporary = (root / ".tmp").resolve()
    if path == temporary or not path.is_relative_to(temporary):
        raise MonitorError(f"窗口证据只允许写入仓库 .tmp 内：{path}")
    return path


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _windows_api() -> tuple[Any, Any, Any]:
    if os.name != "nt":
        raise MonitorError("windows_window_monitor 只支持 Windows")
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(
        None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
        wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD,
    )
    signatures = [
        (user.SetWinEventHook, [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
                               callback, wintypes.DWORD, wintypes.DWORD,
                               wintypes.DWORD], wintypes.HANDLE),
        (user.UnhookWinEvent, [wintypes.HANDLE], wintypes.BOOL),
        (user.GetWindowThreadProcessId,
         [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        (user.IsWindowVisible, [wintypes.HWND], wintypes.BOOL),
        (user.GetWindowTextW,
         [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user.GetClassNameW,
         [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user.PeekMessageW,
         [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
          wintypes.UINT, wintypes.UINT], wintypes.BOOL),
        (user.TranslateMessage, [ctypes.POINTER(wintypes.MSG)], wintypes.BOOL),
        (user.DispatchMessageW, [ctypes.POINTER(wintypes.MSG)], ctypes.c_ssize_t),
        (kernel.CreateToolhelp32Snapshot,
         [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        (kernel.Process32FirstW,
         [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)], wintypes.BOOL),
        (kernel.Process32NextW,
         [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)], wintypes.BOOL),
        (kernel.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
    ]
    for function, arguments, result in signatures:
        function.argtypes = arguments
        function.restype = result
    return user, kernel, callback


class ProcessInventory:
    """Keep observed process instances, not one ever-growing map keyed by PID.

    A PID reused after an observed exit must not inherit the earlier process's
    ancestry. Parent-instance links are fixed when the child is first observed.
    """

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.current: dict[int, dict[str, Any]] = {}
        self.command_roots: dict[int, dict[str, Any]] = {}

    def _add(self, pid: int, ppid: int, name: str, at: float) -> dict[str, Any]:
        item = {
            "instance": len(self.records) + 1, "pid": pid, "ppid": ppid,
            "name": name, "first_seen": at, "last_seen": at,
            "parent_instance": None,
        }
        self.records.append(item)
        self.current[pid] = item
        return item

    def mark_command(self, pid: int, name: str, at: float) -> int:
        item = self.current.get(pid)
        sampled = item is not None and item["first_seen"] >= at
        if item is None or item["first_seen"] < at:
            item = self._add(pid, os.getpid(), name, at)
        else:
            # A Toolhelp sample can win the race immediately after Popen.
            item["first_seen"] = at
        item["command_root"] = True
        item["sampled"] = sampled
        self.command_roots[pid] = item
        return item["instance"]

    def retire_command(self, pid: int, at: float) -> None:
        item = self.command_roots.pop(pid, None)
        if item is not None:
            item["retired_at"] = at
            if self.current.get(pid) is item:
                self.current.pop(pid)

    def observe(self, rows: list[tuple[int, int, str]], at: float) -> None:
        present = {row[0] for row in rows}
        for pid, item in list(self.command_roots.items()):
            # Before the first sample an empty snapshot can predate Popen.
            # Once observed, absence confirms exit and permanently retires
            # this root instance; later reuse of its PID is a new process.
            if item["sampled"] and pid not in present:
                self.retire_command(pid, at)
        self.current = {pid: item for pid, item in self.current.items() if pid in present}
        added: list[dict[str, Any]] = []
        for pid, ppid, name in rows:
            item = self.current.get(pid) or self.command_roots.get(pid)
            if item and item.get("command_root"):
                self.current[pid] = item
                item.update(ppid=ppid, name=name, last_seen=at, sampled=True)
            elif not item or (item["ppid"], item["name"]) != (ppid, name):
                added.append(self._add(pid, ppid, name, at))
            else:
                item["last_seen"] = at
        for item in added:
            parent = self.current.get(item["ppid"]) or self.command_roots.get(item["ppid"])
            if parent is not None:
                item["parent_instance"] = parent["instance"]


def classify_events(
    events: list[dict[str, Any]], processes: list[dict[str, Any]],
    command_instance: int | None,
) -> list[dict[str, Any]]:
    by_instance = {item["instance"]: item for item in processes}
    command = by_instance.get(command_instance)
    result = []
    for event in events:
        matches = [
            item for item in processes
            if item["pid"] == event["pid"]
            and item["first_seen"] - 2 * POLL_INTERVAL <= event["at"]
            <= item["last_seen"] + 2 * POLL_INTERVAL
        ]
        attribution = "unattributed"
        if len(matches) == 1 and command is not None:
            item = matches[0]
            seen = set()
            while item and item["instance"] not in seen:
                seen.add(item["instance"])
                if item["instance"] == command_instance:
                    attribution = "command_tree"
                    break
                if item["first_seen"] < command["first_seen"] or item["ppid"] == 0:
                    attribution = "other_processes"
                    break
                item = by_instance.get(item["parent_instance"])
        result.append({**event, "attribution": attribution})
    return result


def event_summary(events: list[dict[str, Any]], *, monitor_ok: bool) -> dict[str, Any]:
    terminal = [
        event for event in events
        if any(token in event["class"].lower() for token in ("console", "terminal", "cascadia"))
    ]
    summary: dict[str, Any] = {"monitor_ok": monitor_ok, "window_event_count": len(events)}
    for group in ("command_tree", "other_processes", "unattributed"):
        selected = [event for event in terminal if event["attribution"] == group]
        # Failed hooks must never produce an apparently successful zero result.
        summary[f"{group}_terminal_events"] = len(selected) if monitor_ok else None
        summary[f"{group}_visible_terminal_shows"] = (
            sum(event["event"] == EVENT_OBJECT_SHOW and event["visible"] for event in selected)
            if monitor_ok else None
        )
    return summary


class WindowMonitor:
    def __init__(self, *, api: tuple[Any, Any, Any] | None = None) -> None:
        self.user, self.kernel, self.callback_type = api or _windows_api()
        self.started = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self.processes = ProcessInventory()
        self.command_instance: int | None = None
        self.errors: list[str] = []
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.hook_ready = threading.Event()
        self.process_ready = threading.Event()
        self.threads: list[threading.Thread] = []

    def _snapshot(self) -> list[tuple[int, int, str]]:
        handle = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise MonitorError("CreateToolhelp32Snapshot 失败")
        rows = []
        try:
            item = ProcessEntry()
            item.dwSize = ctypes.sizeof(item)
            if not self.kernel.Process32FirstW(handle, ctypes.byref(item)):
                raise MonitorError("Process32FirstW 失败")
            while True:
                rows.append((int(item.th32ProcessID), int(item.th32ParentProcessID), item.szExeFile))
                if not self.kernel.Process32NextW(handle, ctypes.byref(item)):
                    break
        finally:
            self.kernel.CloseHandle(handle)
        return rows

    def _process_loop(self) -> None:
        try:
            while not self.stopping.is_set():
                rows = self._snapshot()
                with self.lock:
                    self.processes.observe(rows, time.monotonic() - self.started)
                self.process_ready.set()
                self.stopping.wait(POLL_INTERVAL)
        except Exception as error:  # noqa: BLE001 - record callback/thread failures and finish teardown
            self.errors.append(f"进程观测失败：{error}")
            self.process_ready.set()

    def _hook_loop(self) -> None:
        handles = []

        @self.callback_type
        def callback(_hook, event, hwnd, obj, _child, _thread, milliseconds):
            if not hwnd or obj != 0:
                return
            try:
                pid = wintypes.DWORD()
                self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                title, kind = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(256)
                self.user.GetWindowTextW(hwnd, title, len(title))
                self.user.GetClassNameW(hwnd, kind, len(kind))
                self.events.append({
                    "event": int(event), "hwnd": int(hwnd), "pid": pid.value,
                    "visible": bool(self.user.IsWindowVisible(hwnd)),
                    "class": kind.value, "title": title.value,
                    "windows_time_ms": int(milliseconds),
                    "at": time.monotonic() - self.started,
                })
            except Exception as error:  # noqa: BLE001 - record callback/thread failures and finish teardown
                self.errors.append(f"WinEvent 回调失败：{error}")

        try:
            for low, high in ((EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND),
                              (EVENT_OBJECT_CREATE, EVENT_OBJECT_HIDE)):
                handle = self.user.SetWinEventHook(low, high, None, callback, 0, 0, 0)
                if not handle:
                    raise MonitorError(f"SetWinEventHook({low:#x}, {high:#x}) 失败")
                handles.append(handle)
            self.hook_ready.set()
            message = wintypes.MSG()
            while not self.stopping.is_set():
                while self.user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    self.user.TranslateMessage(ctypes.byref(message))
                    self.user.DispatchMessageW(ctypes.byref(message))
                self.stopping.wait(0.002)
        except Exception as error:  # noqa: BLE001 - record callback/thread failures and finish teardown
            self.errors.append(f"窗口监听失败：{error}")
        finally:
            self.hook_ready.set()
            for handle in handles:
                if not self.user.UnhookWinEvent(handle):
                    self.errors.append("UnhookWinEvent 失败")

    def start(self) -> None:
        self.threads = [
            threading.Thread(target=target, daemon=True)
            for target in (self._hook_loop, self._process_loop)
        ]
        for thread in self.threads:
            thread.start()
        if not self.hook_ready.wait(5) or not self.process_ready.wait(5):
            self.errors.append("窗口／进程监听初始化超时")
        if self.errors:
            self.stop()
            raise MonitorError("；".join(self.errors))

    def mark_command(self, pid: int, name: str, at: float) -> None:
        with self.lock:
            self.command_instance = self.processes.mark_command(pid, name, at)

    def command_exited(self, pid: int) -> None:
        with self.lock:
            self.processes.retire_command(pid, time.monotonic() - self.started)

    def stop(self) -> None:
        self.stopping.set()
        for thread in self.threads:
            thread.join(5)
            if thread.is_alive():
                self.errors.append("监听线程未正常结束")

    def record(self) -> dict[str, Any]:
        events = classify_events(self.events, self.processes.records, self.command_instance)
        return {
            "summary": event_summary(events, monitor_ok=not self.errors),
            "monitor_errors": self.errors,
            "events": events, "processes": self.processes.records,
            "command_instance": self.command_instance,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--cwd", type=Path, default=ROOT)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    command = options.command[1:] if options.command[:1] == ["--"] else options.command
    if not command:
        parser.error("需要在 -- 后指定 argv 命令")
    try:
        output = evidence_path(options.out)
    except MonitorError as error:
        parser.error(str(error))
    monitor = None
    code = 2
    command_pid = None
    command_returncode = None
    error = None
    started = time.monotonic()
    try:
        monitor = WindowMonitor()
        monitor.start()
        launch_at = time.monotonic() - monitor.started
        child = subprocess.Popen(
            command, cwd=options.cwd.resolve(), stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        command_pid = child.pid
        monitor.mark_command(child.pid, Path(command[0]).name, launch_at)
        code = child.wait()
        monitor.command_exited(child.pid)
        command_returncode = code
        time.sleep(0.3)
    except (MonitorError, OSError) as caught:
        error = str(caught)
    finally:
        if monitor is not None:
            monitor.stop()
    record = monitor.record() if monitor is not None else {
        "summary": event_summary([], monitor_ok=False), "monitor_errors": [],
        "events": [], "processes": [], "command_instance": None,
    }
    if error:
        record["monitor_errors"].append(error)
        record["summary"] = event_summary(record["events"], monitor_ok=False)
    record.update(command=command, command_pid=command_pid, command_returncode=command_returncode)
    record["summary"].update(
        duration_seconds=round(time.monotonic() - started, 3), command_returncode=command_returncode,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("WIN_EVENT_MONITOR " + json.dumps(record["summary"], ensure_ascii=False), file=sys.stderr)
    if record["monitor_errors"]:
        print("ERROR: " + "；".join(record["monitor_errors"]), file=sys.stderr)
    return code if code else (0 if record["summary"]["monitor_ok"] else 2)


if __name__ == "__main__":
    raise SystemExit(main())
