from __future__ import annotations

import argparse
import json
import os
import signal
import socket
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback
from urllib import error, request
import webbrowser
from urllib.parse import urlparse
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
LOCAL = ROOT / ".local"
LOGS = LOCAL / "logs"
CONFIG = LOCAL / "launcher.json"
BACKEND_URL = "http://127.0.0.1:8000/api/health"
FRONTEND_URL = "http://127.0.0.1:5173"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.remote_preview import TailscalePreview
from backend.runtime_version import API_CONTRACT, source_signature, installation_id

RUNTIME_STATE = LOCAL / 'backend-runtime.json'


def backend_identity(result) -> dict | None:
    if not result or result[0] != 200:
        return None
    try:
        payload = json.loads(result[1])
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) and payload.get('status') == 'ok' else None


def backend_is_current(result) -> bool:
    payload = backend_identity(result)
    runtime = payload.get('runtime') if payload else None
    return bool(isinstance(runtime, dict) and runtime.get('contract') == API_CONTRACT
        and runtime.get('source_signature') == source_signature()
        and runtime.get('installation_id') == installation_id()
        and not payload.get('restart_required'))


def frontend_is_current(result) -> bool:
    if not result or result[0] != 200:
        return False
    class CoreMarker(HTMLParser):
        found = False
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == 'meta' and values.get('name') == 'avelin-app' and values.get('content') == 'core-v2':
                self.found = True
    marker = CoreMarker()
    marker.feed(result[1])
    return marker.found


def stop_managed_backend(result) -> bool:
    """Only an exact instance previously recorded by this launcher may be stopped."""
    payload = backend_identity(result)
    runtime = payload.get('runtime') if payload else None
    if not isinstance(runtime, dict) or not RUNTIME_STATE.is_file():
        return False
    try:
        saved = json.loads(RUNTIME_STATE.read_text(encoding='utf8'))
        pid = int(runtime.get('process_id', 0))
        if (not runtime.get('launcher_id') or not runtime.get('instance_id')
                or saved.get('launcher_id') != runtime.get('launcher_id')
                or saved.get('instance_id') != runtime.get('instance_id')
                or saved.get('installation_id') != installation_id()
                or runtime.get('installation_id') != installation_id()
                or saved.get('process_id') != pid or pid <= 1 or pid == os.getpid()):
            return False
        os.kill(pid, signal.SIGTERM)
        RUNTIME_STATE.unlink(missing_ok=True)
        return True
    except (ValueError, OSError, TypeError):
        return False


def launcher_log(message: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with (LOGS / "launcher.log").open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def load_paths() -> tuple[Path, Path]:
    configured: dict[str, str] = {}
    if CONFIG.is_file():
        configured = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    python_path = Path(configured.get("python", ROOT / ".venv" / "Scripts" / "python.exe"))
    configured_node = Path(configured.get("node", "")) if configured.get("node") else Path()
    node_raw = str(configured_node) if configured_node.is_file() else (shutil.which("node") or "")
    return python_path, Path(node_raw) if node_raw else Path()


def fetch(url: str, timeout: float = 1.5) -> tuple[int, str] | None:
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status, response.read(4096).decode("utf-8", errors="replace")
    except (error.URLError, OSError, TimeoutError):
        return None


def wait_for(url: str, predicate, timeout: float = 25.0, *, cancel=None, keep_waiting=None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            return False
        if keep_waiting is not None and not keep_waiting():
            return False
        result = fetch(url)
        if result is not None and predicate(*result):
            return True
        if cancel is None:
            time.sleep(0.25)
        else:
            cancel.wait(0.25)
    return False


def terminate_owned_process_tree(process: subprocess.Popen) -> bool:
    """The caller supplies only Popen objects created by this launcher."""
    if process.poll() is not None:
        return True
    try:
        if os.name == 'nt':
            completed = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                creationflags=NO_WINDOW, check=False)
            if completed.returncode != 0 and process.poll() is None:
                return False
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Still only this live, owned Popen handle, never an arbitrary PID.
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


class AvelinLauncher:
    def __init__(self) -> None:
        self.python, self.node = load_paths()
        self.processes: list[subprocess.Popen] = []
        self.log_files = []
        self.reused = False
        self.backend_instance: dict | None = None
        self._lifecycle_lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._closed = threading.Event()

    def validate(self) -> None:
        missing = []
        if not self.python.is_file():
            missing.append(f"Python: {self.python}")
        if not self.node.is_file():
            missing.append(f"Node.js: {self.node or 'не найден'}")
        vite = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
        if not vite.is_file():
            missing.append(f"Vite: {vite}")
        if missing:
            raise RuntimeError("Не найдены компоненты запуска:\n" + "\n".join(missing))

    def start(self) -> str:
        with self._lifecycle_lock:
            self._check_cancelled()
            try:
                return self._start_locked()
            except BaseException:
                self._cleanup_owned()
                raise

    def _check_cancelled(self) -> None:
        if self._stop_requested.is_set() or self._closed.is_set():
            raise RuntimeError('Запуск отменён: лаунчер остановлен.')

    def _start_locked(self) -> str:
        self.validate()
        backend = fetch(BACKEND_URL)
        frontend = fetch(FRONTEND_URL)
        backend_ready = backend_is_current(backend)
        if backend_identity(backend) and not backend_ready:
            if stop_managed_backend(backend):
                launcher_log('stale managed backend stopped; starting current source')
                if not wait_for_backend_stop():
                    raise RuntimeError('Прежний backend ещё завершает работу. Повтори запуск через несколько секунд.')
                backend = None
            else:
                raise RuntimeError('Работает устаревший backend Avelin. Закрой прежний лаунчер и запусти его заново. '
                    'Проверка версии не разрешает использовать старый сервер с новым интерфейсом.')
        self._check_cancelled()
        frontend_ready = frontend_is_current(frontend)
        launcher_log(
            f"start requested; backend={'ready' if backend_ready else 'missing' if backend is None else 'occupied'}; "
            f"frontend={'ready' if frontend_ready else 'missing' if frontend is None else 'occupied'}"
        )
        if backend_ready and frontend_ready:
            if not self.processes and not self.backend_instance:
                self.reused = True
            return "Avelin уже запущен — использую работающий экземпляр."
        if backend is not None and not backend_ready:
            raise RuntimeError("Порт 8000 занят другим процессом. Закрой его или перезагрузи компьютер.")
        if frontend is not None and not frontend_ready:
            raise RuntimeError("Порт 5173 занят другим процессом. Закрой его или перезагрузи компьютер.")

        LOGS.mkdir(parents=True, exist_ok=True)
        backend_log = (LOGS / "backend-launcher.log").open("a", encoding="utf-8")
        frontend_log = (LOGS / "frontend-launcher.log").open("a", encoding="utf-8")
        self.log_files.extend([backend_log, frontend_log])
        backend_process = frontend_process = None
        launch_id = uuid4().hex
        if not backend_ready:
            self._check_cancelled()
            environment = os.environ.copy()
            environment['AVELIN_LAUNCH_ID'] = launch_id
            backend_process = subprocess.Popen(
                    [
                        str(self.python), "-m", "uvicorn", "backend.main:app",
                        "--host", "127.0.0.1", "--port", "8000",
                    ],
                    cwd=ROOT,
                    stdout=backend_log,
                    stderr=subprocess.STDOUT,
                    creationflags=NO_WINDOW,
                    env=environment,
                )
            self.processes.append(backend_process)
        if not frontend_ready:
            self._check_cancelled()
            frontend_process = subprocess.Popen(
                    [
                        str(self.node),
                        str(FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"),
                        "--host", "127.0.0.1", "--port", "5173",
                    ],
                    cwd=FRONTEND,
                    stdout=frontend_log,
                    stderr=subprocess.STDOUT,
                    creationflags=NO_WINDOW,
                )
            self.processes.append(frontend_process)

        def owned_backend_ready(status, body):
            self._check_cancelled()
            if backend_process is None or backend_process.poll() is not None:
                return False
            result = (status, body)
            payload = backend_identity(result)
            runtime = payload.get('runtime') if payload else None
            if not backend_is_current(result) or not isinstance(runtime, dict) or runtime.get('launcher_id') != launch_id:
                return False
            # Capture the exact validated response, never a later independent fetch.
            self.backend_instance = dict(runtime)
            return True

        backend_ok = backend_ready or wait_for(BACKEND_URL, owned_backend_ready,
            cancel=self._stop_requested, keep_waiting=lambda: backend_process.poll() is None)
        if backend_ok and not backend_ready:
            if not self.backend_instance:
                raise RuntimeError('Backend не подтвердил принадлежность этому запуску.')
            RUNTIME_STATE.parent.mkdir(parents=True, exist_ok=True)
            temporary = RUNTIME_STATE.with_name(f'{RUNTIME_STATE.name}.{launch_id}.tmp')
            temporary.write_text(json.dumps(self.backend_instance), encoding='utf8')
            os.replace(temporary, RUNTIME_STATE)
        self._check_cancelled()
        frontend_ok = frontend_ready or wait_for(FRONTEND_URL, lambda status, body: frontend_is_current((status, body)),
            cancel=self._stop_requested, keep_waiting=lambda: frontend_process.poll() is None)
        self._check_cancelled()
        if not backend_ok or not frontend_ok:
            launcher_log(f"startup timeout; backend_ok={backend_ok}; frontend_ok={frontend_ok}")
            raise RuntimeError("Сервисы не стали готовы. Проверь .local/logs/*.log.")
        started = []
        if not backend_ready:
            started.append("backend")
        if not frontend_ready:
            started.append("frontend")
        launcher_log(f"startup completed; started={','.join(started)}")
        if len(started) == 1:
            return f"Avelin готов — восстановлен {started[0]}."
        return "Backend и frontend готовы."

    def stop(self) -> None:
        self.request_stop()
        with self._lifecycle_lock:
            self._cleanup_owned()
            if self.processes:
                raise RuntimeError('Не удалось остановить все процессы Avelin. Проверь логи и повтори остановку.')

    def request_stop(self, *, permanent: bool = False) -> None:
        # Set before taking the lifecycle lock so an in-flight startup can exit.
        if permanent:
            self._closed.set()
        self._stop_requested.set()

    def _cleanup_owned(self) -> None:
        if not self.reused:
            remaining = []
            for process in reversed(self.processes):
                if not terminate_owned_process_tree(process):
                    remaining.append(process)
            self.processes = list(reversed(remaining))
            # Kill owned trees before their backend root, so descendants cannot
            # escape when a venv redirector waits for the actual Python child.
            if self.backend_instance and not self.processes:
                current = backend_identity(fetch(BACKEND_URL, timeout=.4))
                runtime = current.get('runtime', {}) if current else {}
                keys = ('instance_id', 'launcher_id', 'installation_id', 'process_id')
                if all(runtime.get(key) == self.backend_instance.get(key) for key in keys):
                    stop_managed_backend((200, json.dumps(current)))
                elif not self.processes and RUNTIME_STATE.is_file():
                    try:
                        saved = json.loads(RUNTIME_STATE.read_text(encoding='utf8'))
                        if all(saved.get(key) == self.backend_instance.get(key) for key in keys):
                            RUNTIME_STATE.unlink(missing_ok=True)
                    except (ValueError, OSError):
                        pass
                if not self.processes:
                    self.backend_instance = None
        else:
            self.processes.clear()
        for handle in self.log_files:
            handle.close()
        self.log_files.clear()

    def restart(self) -> str:
        # Only an explicit restart clears cancellation. A later stop wins.
        self._stop_requested.clear()
        with self._lifecycle_lock:
            self._check_cancelled()
            if self.reused:
                if not stop_managed_backend(fetch(BACKEND_URL)):
                    raise RuntimeError('Этот backend не принадлежит лаунчеру. Закрой исходный экземпляр перед перезапуском.')
                if not wait_for_backend_stop():
                    raise RuntimeError('Backend ещё останавливается. Повтори запуск через несколько секунд.')
            self._cleanup_owned()
            if self.processes:
                raise RuntimeError('Прежние процессы ещё работают; перезапуск отменён.')
            self.reused = False
            self.backend_instance = None
            return self.start()


def wait_for_backend_stop(timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    target = urlparse(BACKEND_URL)
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((target.hostname or '127.0.0.1', target.port or 80), timeout=.25):
                pass
        except OSError:
            return True
        time.sleep(0.1)
    return False


def provider_label() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from ai_agent.config import Settings

    settings = Settings.load(ROOT)
    return f"{settings.model_provider} · {settings.model_name}"


def run_check() -> int:
    launcher = AvelinLauncher()
    try:
        message = launcher.start()
        print(json.dumps({"ok": True, "message": message, "provider": provider_label()}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        launcher.stop()


def run_remote_check() -> int:
    preview = TailscalePreview()
    state = preview.status()
    payload = preview.smoke() if state.active else state.payload()
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("installed") and payload.get("online") else 1


def run_remote_stop() -> int:
    state = TailscalePreview().stop()
    print(json.dumps(state.payload(), ensure_ascii=False))
    return 0


def run_gui() -> int:
    import tkinter as tk
    from tkinter import messagebox

    launcher = AvelinLauncher()
    remote = TailscalePreview()
    window = tk.Tk()
    window.title("Avelin Core Agent")
    window.geometry("620x420")
    window.resizable(False, False)
    window.configure(bg="#f3f0e8")
    closing = threading.Event()
    remote_lock = threading.Lock()

    def ui(callback) -> None:
        if not closing.is_set():
            try:
                window.after(0, lambda: callback() if not closing.is_set() else None)
            except (RuntimeError, tk.TclError):
                pass

    status = tk.StringVar(value="Запускаю Avelin…")

    tk.Label(window, text="AVELIN", font=("Segoe UI", 28, "bold"), bg="#f3f0e8", fg="#191919").pack(pady=(28, 4))
    tk.Label(window, text=provider_label(), font=("Segoe UI", 10), bg="#f3f0e8", fg="#666666").pack()
    tk.Label(window, textvariable=status, wraplength=540, justify="center", font=("Segoe UI", 11), bg="#f3f0e8", fg="#333333").pack(pady=(22, 16))
    buttons = tk.Frame(window, bg="#f3f0e8")
    buttons.pack()
    open_button = tk.Button(buttons, text="Открыть Avelin", width=18, state="disabled", command=lambda: webbrowser.open(FRONTEND_URL))
    open_button.pack(side="left", padx=6)
    tk.Button(buttons, text="Открыть логи", width=14, command=lambda: subprocess.Popen(["explorer.exe", str(LOGS)], creationflags=NO_WINDOW)).pack(side="left", padx=6)
    restart_button = tk.Button(buttons, text='Перезапустить', width=14, state='disabled')
    restart_button.pack(side='left', padx=6)

    remote_frame = tk.Frame(window, bg="#e8e4da", padx=16, pady=12)
    remote_frame.pack(fill="x", padx=34, pady=(24, 0))
    remote_status = tk.StringVar(value="Проверяю Tailscale…")
    remote_url = tk.StringVar(value="")
    tk.Label(remote_frame, text="PRIVATE PREVIEW", font=("Segoe UI", 9, "bold"), bg="#e8e4da", fg="#666666").grid(row=0, column=0, sticky="w")
    tk.Label(remote_frame, textvariable=remote_status, wraplength=520, justify="left", font=("Segoe UI", 10), bg="#e8e4da", fg="#242424").grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 2))
    tk.Label(remote_frame, textvariable=remote_url, font=("Segoe UI", 9), bg="#e8e4da", fg="#555555").grid(row=2, column=0, columnspan=3, sticky="w")

    def copy_remote_url() -> None:
        if remote_url.get():
            window.clipboard_clear()
            window.clipboard_append(remote_url.get())

    remote_button = tk.Button(remote_frame, text="Включить", width=16, state="disabled")
    remote_button.grid(row=3, column=0, sticky="w", pady=(10, 0))
    open_remote_button = tk.Button(remote_frame, text="Открыть", width=10, state="disabled", command=lambda: webbrowser.open(remote_url.get()))
    open_remote_button.grid(row=3, column=1, padx=6, pady=(10, 0))
    copy_button = tk.Button(remote_frame, text="Копировать URL", width=14, state="disabled", command=copy_remote_url)
    copy_button.grid(row=3, column=2, pady=(10, 0))

    def render_remote_state(remote_state) -> None:
        remote_status.set(remote_state.message)
        remote_url.set(remote_state.url)
        if not remote_state.installed or not remote_state.online:
            remote_button.configure(state="disabled", text="Включить")
        else:
            remote_button.configure(state="normal", text="Остановить доступ" if remote_state.active else "Включить")
        target_state = "normal" if remote_state.active and remote_state.url else "disabled"
        open_remote_button.configure(state=target_state)
        copy_button.configure(state=target_state)

    def toggle_remote_worker() -> None:
        try:
            with remote_lock:
                if closing.is_set():
                    return
                current = remote.status()
                if closing.is_set():
                    return
                pending = "Останавливаю private preview…" if current.active else "Запускаю HTTPS private preview…"
                ui(lambda: remote_status.set(pending))
                remote_state = remote.stop() if current.active else remote.start()
            ui(lambda: render_remote_state(remote_state))
        except Exception as exc:
            launcher_log(f"remote preview failed: {exc}")
            remote_error = f"Remote preview недоступен: {exc}"
            ui(lambda: remote_status.set(remote_error))

    remote_button.configure(command=lambda: threading.Thread(target=toggle_remote_worker, daemon=True).start())

    def close_window() -> None:
        if closing.is_set():
            return
        closing.set()
        launcher.request_stop(permanent=True)
        status.set('Останавливаю Avelin…')
        open_button.configure(state='disabled')
        restart_button.configure(state='disabled')
        remote_button.configure(state='disabled')

        def finish_close() -> None:
            try:
                try:
                    with remote_lock:
                        if remote.started_here:
                            remote.stop()
                finally:
                    launcher.stop()
            except Exception as exc:
                launcher_log(f'launcher stop failed: {exc}')
                message = f'Не удалось завершить остановку: {exc}'
                def failed():
                    closing.clear()
                    status.set(message)
                window.after(0, failed)
            else:
                window.after(0, window.destroy)
        # Keep the Tk loop responsive while startup cancellation and tree cleanup finish.
        threading.Thread(target=finish_close, daemon=False).start()

    tk.Button(buttons, text="Остановить", width=12, command=close_window).pack(side="left", padx=6)
    window.protocol("WM_DELETE_WINDOW", close_window)

    starting = threading.Event()
    start_lock = threading.Lock()

    def start_worker(restart: bool = False) -> None:
        if closing.is_set() or not start_lock.acquire(blocking=False):
            return
        starting.set()
        try:
            if closing.is_set():
                return
            ui(lambda: restart_button.configure(state='disabled'))
            ui(lambda: open_button.configure(state='disabled'))
            message = launcher.restart() if restart else launcher.start()
            with remote_lock:
                remote_state = remote.status() if not closing.is_set() else None
            ui(lambda: status.set(message))
            ui(lambda: open_button.configure(state="normal"))
            if remote_state is not None:
                ui(lambda: render_remote_state(remote_state))
            ui(lambda: webbrowser.open(FRONTEND_URL))
        except Exception as exc:
            launcher_log(f"startup failed: {exc}\n{traceback.format_exc()}")
            error_text = f"Запуск не удался: {exc}"
            ui(lambda: status.set(error_text))
            ui(lambda: messagebox.showerror("Avelin", error_text))
        finally:
            starting.clear()
            start_lock.release()
            ui(lambda: restart_button.configure(state='normal'))

    restart_button.configure(command=lambda: threading.Thread(target=start_worker, args=(True,), daemon=True).start())

    def check_version() -> None:
        while not closing.wait(5):
            if not starting.is_set():
                try:
                    running = fetch(BACKEND_URL, timeout=.4)
                    if backend_identity(running) and not backend_is_current(running):
                        ui(lambda: status.set('Код обновлён. Нажми «Перезапустить», чтобы backend загрузил изменения.'))
                except OSError as exc:
                    launcher_log(f'version check failed: {exc}')

    threading.Thread(target=start_worker, daemon=True).start()
    threading.Thread(target=check_version, daemon=True).start()
    window.mainloop()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--remote-check", action="store_true")
    parser.add_argument("--remote-stop", action="store_true")
    args = parser.parse_args()
    if args.check:
        raise SystemExit(run_check())
    if args.remote_check:
        raise SystemExit(run_remote_check())
    if args.remote_stop:
        raise SystemExit(run_remote_stop())
    raise SystemExit(run_gui())
