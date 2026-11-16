"""Hidden subprocesses and an isolated agent-browser lifecycle.

On Windows agent-browser's detached daemon launches console-subsystem Chromium
without CREATE_NO_WINDOW. Hiding the CLI alone therefore does not hide Chrome.
We launch our own headless Chrome with a private profile, then use the CLI's
standard CDP attachment; close disconnects that session and reaps only our child.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence  # noqa: UP035 - preserve public typing aliases

ROOT = Path(__file__).resolve().parents[1]


def hidden_process_options() -> dict[str, Any]:
    """Popen/run kwargs; no shell and no visible Windows console."""
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startup}


def run_hidden(command: Sequence[str | Path], **kwargs: Any) -> subprocess.CompletedProcess:
    options = hidden_process_options()
    options.update(kwargs)
    return subprocess.run(command, check=options.pop("check", False), **options)


def resolve_agent_browser(environment: Mapping[str, str]) -> Path:
    """Avoid npm's Windows shim/wrapper, which explicitly sets windowsHide:false."""
    configured = environment.get("TRANSLATOR_AGENT_BROWSER", "agent-browser")
    found = shutil.which(configured, path=environment.get("PATH"))
    path = Path(found or configured).expanduser().resolve()
    if os.name != "nt":
        return path
    arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
    binary = f"agent-browser-win32-{arch}.exe"
    candidates = [
        path.parent / binary,
        path.parent / "node_modules" / "agent-browser" / "bin" / binary,
        path.parent.parent / "node_modules" / "agent-browser" / "bin" / binary,
    ]
    is_mise_shim = path.parent.name.lower() == "shims" and path.parent.parent.name.lower() == "mise"
    if is_mise_shim:
        mise = shutil.which("mise", path=environment.get("PATH"))
        if mise:
            active = run_hidden(
                [mise, "where", "node"], env=dict(environment), cwd=ROOT,
                capture_output=True, text=True, errors="replace", timeout=15,
                check=False,
            )
            if active.returncode == 0:
                candidates.append(
                    Path(active.stdout.strip()) / "node_modules" / "agent-browser" / "bin" / binary
                )
    elif path.suffix.lower() == ".exe":
        return path
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "找不到 agent-browser 原生 Windows CLI；"
        "请将 TRANSLATOR_AGENT_BROWSER 指向安装包 bin 下的原生 .exe"
    )


def resolve_chromium(environment: Mapping[str, str]) -> Path:
    configured = environment.get("TRANSLATOR_BROWSER_EXECUTABLE") or environment.get(
        "AGENT_BROWSER_EXECUTABLE_PATH"
    )
    if configured:
        path = Path(configured).expanduser().resolve()
        if path.is_file():
            return path
        raise FileNotFoundError(f"浏览器可执行文件不存在：{path}")
    # Use only the installed headless-shell cache, never a user's browser/profile.
    local = environment.get("LOCALAPPDATA")
    if local:
        cache = Path(local) / "ms-playwright"
        matches = list(
            cache.glob(
                "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"
            )
        )
        if matches:
            return max(
                matches,
                key=lambda item: int(item.parents[1].name.rsplit("-", 1)[-1]),
            ).resolve()
    raise FileNotFoundError(
        "找不到已安装的 Chromium headless shell；"
        "请用 TRANSLATOR_BROWSER_EXECUTABLE 指定浏览器 .exe"
    )


class AgentBrowserSession:
    """One namespace/session and, on Windows, one owned Chrome process handle."""

    def __init__(
        self,
        namespace: str,
        session: str,
        *,
        root: Path = ROOT,
        environment: Mapping[str, str] | None = None,
        scratch: Path | None = None,
    ) -> None:
        self.namespace = namespace
        self.session = session
        self.root = root
        self.environment = dict(os.environ if environment is None else environment)
        self.browser = resolve_agent_browser(self.environment)
        self.environment["AGENT_BROWSER_HEADED"] = "false"
        self.environment["AGENT_BROWSER_ALLOW_FILE_ACCESS"] = "1"
        self.temporary_scratch = scratch is None
        base = (root / ".tmp" / "translator" / "browser-runtime").resolve()
        if scratch is None:
            base.mkdir(parents=True, exist_ok=True)
            self.scratch = Path(tempfile.mkdtemp(prefix="session-", dir=base))
        else:
            self.scratch = scratch.resolve()
            self.scratch.mkdir(parents=True, exist_ok=True)
        self.environment.update(TEMP=str(self.scratch), TMP=str(self.scratch))
        # Namespace semantics remain native; keep all sidecars inside the project.
        self.environment["AGENT_BROWSER_SOCKET_DIR"] = str(self.scratch / "sockets")
        self.chrome: subprocess.Popen | None = None
        self.cdp: str | None = None
        self.profile: Path | None = None
        self.invoked = False

    def start(self, timeout: float = 30) -> None:
        if os.name != "nt" or self.chrome is not None:
            return
        executable = resolve_chromium(self.environment)
        self.profile = Path(tempfile.mkdtemp(prefix="chrome-", dir=self.scratch))
        arguments = [
            str(executable),
            "--headless=new",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            f"--user-data-dir={self.profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--password-store=basic",
            "--use-mock-keychain",
            "--allow-file-access-from-files",
            "--allow-file-access",
            "--hide-scrollbars",
            "--enable-unsafe-swiftshader",
            "--window-size=1280,720",
        ]
        extra_arguments = [
            arg.strip()
            for arg in re.split(r"[,\n]", self.environment.get("AGENT_BROWSER_ARGS", ""))
            if arg.strip()
        ]
        reserved = ("--user-data-dir", "--remote-debugging-", "--headless", "--load-extension")
        if any(arg.startswith(reserved) for arg in extra_arguments):
            raise RuntimeError("AGENT_BROWSER_ARGS 不能覆盖托管浏览器的 profile、CDP 或 headless 参数")
        arguments.extend(extra_arguments)
        arguments.append("about:blank")
        # A file, not a pipe: the browser outlives the initial CLI invocation.
        with (self.profile / "browser.log").open("wb") as log:
            self.chrome = subprocess.Popen(
                arguments,
                cwd=self.root,
                env=self.environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                **hidden_process_options(),
            )
        active_port = self.profile / "DevToolsActivePort"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.chrome.poll() is not None:
                detail = (self.profile / "browser.log").read_text(
                    encoding="utf-8", errors="replace"
                )
                raise RuntimeError(f"Chromium 启动失败：{detail.strip()}")
            try:
                port, route, *_ = active_port.read_text(encoding="utf-8").splitlines()
                if port.isdigit() and route.startswith("/devtools/browser/"):
                    self.cdp = f"ws://127.0.0.1:{int(port)}{route}"
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.05)
        raise subprocess.TimeoutExpired(arguments, timeout)

    def run(
        self,
        arguments: list[str],
        *,
        timeout: int = 30,
        capture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if arguments != ["close"]:
            self.start(timeout)
        elif not self.invoked:
            self.reap_browser()
            self.clean_temporary_scratch()
            return subprocess.CompletedProcess(arguments, 0, "", "")
        command = [
            str(self.browser),
            "--namespace", self.namespace,
            "--session", self.session,
        ]
        environment = self.environment
        if arguments == ["close"]:
            # The CLI sends a fresh local "launch" before *any* command when
            # executable/args/file-access flags are present. On close this would
            # replace our CDP attachment with an unhidden Chrome. Use a config
            # containing no launch options and retain only daemon-owned options;
            # close also remains usable if the owned browser has crashed.
            config = self.scratch / "close-config.json"
            config.write_text("{}\n", encoding="utf-8")
            command.extend(["--config", str(config)])
            daemon_options = {
                "AGENT_BROWSER_SOCKET_DIR", "AGENT_BROWSER_NAMESPACE",
                "AGENT_BROWSER_SESSION", "AGENT_BROWSER_DEBUG",
                "AGENT_BROWSER_ACTION_POLICY", "AGENT_BROWSER_CONFIRM_ACTIONS",
                "AGENT_BROWSER_IDLE_TIMEOUT_MS", "AGENT_BROWSER_DEFAULT_TIMEOUT",
                "AGENT_BROWSER_NO_AUTO_DIALOG",
            }
            environment = {
                key: value for key, value in self.environment.items()
                if not key.startswith("AGENT_BROWSER_") or key in daemon_options
            }
        elif self.cdp:
            command.extend(["--cdp", self.cdp])
        command.extend(arguments)
        output = {"capture_output": True} if capture else {
            "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
        }
        closed = False
        try:
            # Keep capture=False on first daemon startup. The daemon must never
            # inherit a Python capture pipe that keeps communicate() waiting.
            self.invoked = True
            result = run_hidden(
                command,
                cwd=self.root,
                env=environment,
                stdin=subprocess.DEVNULL,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
                **output,
            )
            if arguments == ["close"] and not result.returncode:
                # The native daemon removes its PID/port on exit, but leaves
                # .config behind. Wait for active sidecars before removing our
                # own default scratch; caller evidence is instead bound to clean.
                deadline = time.monotonic() + 5
                while any((self.scratch / "sockets").rglob("*.pid")) or any(
                    (self.scratch / "sockets").rglob("*.port")
                ):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("agent-browser close 后守护进程仍未退出")
                    time.sleep(0.05)
                closed = True
            return result
        finally:
            if arguments == ["close"]:
                self.reap_browser()
                if closed:
                    self.clean_temporary_scratch()

    def reap_browser(self) -> None:
        """Close only the owned child handle; never taskkill a process name/tree."""
        if self.chrome is not None:
            if self.chrome.poll() is None:
                self.chrome.terminate()
            self.chrome.wait(timeout=15)
            self.chrome = None
        if self.profile is not None:
            profile = self.profile.resolve()
            if profile.parent != self.scratch.resolve() or not profile.name.startswith("chrome-"):
                raise RuntimeError(f"拒绝清理托管 profile 以外的目录：{profile}")
            log = profile / "browser.log"
            if log.exists():
                shutil.copyfile(log, self.scratch / "browser.log")
            shutil.rmtree(profile)
            self.profile = None

    def clean_temporary_scratch(self) -> None:
        if not self.temporary_scratch:
            return
        base = (self.root / ".tmp" / "translator" / "browser-runtime").resolve()
        scratch = self.scratch.resolve()
        if scratch.parent != base or not scratch.name.startswith("session-"):
            raise RuntimeError(f"拒绝清理托管 session 以外的目录：{scratch}")
        if scratch.exists():
            shutil.rmtree(scratch)


_SESSIONS: dict[tuple[str, str], AgentBrowserSession] = {}


def run_agent(
    namespace: str,
    session: str,
    arguments: list[str],
    *,
    timeout: int = 30,
    capture: bool = True,
    scratch: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    key = (namespace, session)
    runtime = _SESSIONS.get(key)
    if runtime is None:
        if arguments == ["close"]:
            return subprocess.CompletedProcess(arguments, 0, "", "")
        runtime = _SESSIONS[key] = AgentBrowserSession(namespace, session, scratch=scratch)
    result = runtime.run(arguments, timeout=timeout, capture=capture)
    if arguments == ["close"] and not result.returncode:
        _SESSIONS.pop(key, None)
    return result
