from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback
from urllib import error, request
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
LOCAL = ROOT / ".local"
LOGS = LOCAL / "logs"
CONFIG = LOCAL / "launcher.json"
BACKEND_URL = "http://127.0.0.1:8000/api/health"
FRONTEND_URL = "http://127.0.0.1:5173"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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


def wait_for(url: str, predicate, timeout: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fetch(url)
        if result is not None and predicate(*result):
            return True
        time.sleep(0.25)
    return False


class AvelinLauncher:
    def __init__(self) -> None:
        self.python, self.node = load_paths()
        self.processes: list[subprocess.Popen] = []
        self.log_files = []
        self.reused = False

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
        self.validate()
        backend = fetch(BACKEND_URL)
        frontend = fetch(FRONTEND_URL)
        backend_ready = bool(backend and backend[0] == 200 and '"status":"ok"' in backend[1].replace(" ", ""))
        frontend_ready = bool(frontend and frontend[0] == 200 and "Avelin" in frontend[1])
        launcher_log(
            f"start requested; backend={'ready' if backend_ready else 'missing' if backend is None else 'occupied'}; "
            f"frontend={'ready' if frontend_ready else 'missing' if frontend is None else 'occupied'}"
        )
        if backend_ready and frontend_ready:
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
        if not backend_ready:
            self.processes.append(
                subprocess.Popen(
                    [
                        str(self.python), "-m", "uvicorn", "backend.main:app",
                        "--host", "127.0.0.1", "--port", "8000",
                    ],
                    cwd=ROOT,
                    stdout=backend_log,
                    stderr=subprocess.STDOUT,
                    creationflags=NO_WINDOW,
                )
            )
        if not frontend_ready:
            self.processes.append(
                subprocess.Popen(
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
            )
        backend_ok = backend_ready or wait_for(BACKEND_URL, lambda status, body: status == 200 and '"ok"' in body)
        frontend_ok = frontend_ready or wait_for(FRONTEND_URL, lambda status, body: status == 200 and "Avelin" in body)
        if not backend_ok or not frontend_ok:
            launcher_log(f"startup timeout; backend_ok={backend_ok}; frontend_ok={frontend_ok}")
            self.stop()
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
        if not self.reused:
            for process in reversed(self.processes):
                if process.poll() is None:
                    process.terminate()
            for process in reversed(self.processes):
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        self.processes.clear()
        for handle in self.log_files:
            handle.close()
        self.log_files.clear()


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


def run_gui() -> int:
    import tkinter as tk
    from tkinter import messagebox

    launcher = AvelinLauncher()
    window = tk.Tk()
    window.title("Avelin Core Agent")
    window.geometry("520x290")
    window.resizable(False, False)
    window.configure(bg="#f3f0e8")
    status = tk.StringVar(value="Запускаю Avelin…")

    tk.Label(window, text="AVELIN", font=("Segoe UI", 28, "bold"), bg="#f3f0e8", fg="#191919").pack(pady=(28, 4))
    tk.Label(window, text=provider_label(), font=("Segoe UI", 10), bg="#f3f0e8", fg="#666666").pack()
    tk.Label(window, textvariable=status, wraplength=450, justify="center", font=("Segoe UI", 11), bg="#f3f0e8", fg="#333333").pack(pady=24)
    buttons = tk.Frame(window, bg="#f3f0e8")
    buttons.pack()
    open_button = tk.Button(buttons, text="Открыть Avelin", width=18, state="disabled", command=lambda: webbrowser.open(FRONTEND_URL))
    open_button.pack(side="left", padx=6)
    tk.Button(buttons, text="Открыть логи", width=14, command=lambda: subprocess.Popen(["explorer.exe", str(LOGS)], creationflags=NO_WINDOW)).pack(side="left", padx=6)

    def close_window() -> None:
        launcher.stop()
        window.destroy()

    tk.Button(buttons, text="Остановить", width=12, command=close_window).pack(side="left", padx=6)
    window.protocol("WM_DELETE_WINDOW", close_window)

    def start_worker() -> None:
        try:
            message = launcher.start()
            window.after(0, lambda: status.set(message))
            window.after(0, lambda: open_button.configure(state="normal"))
            window.after(0, lambda: webbrowser.open(FRONTEND_URL))
        except Exception as exc:
            launcher_log(f"startup failed: {exc}\n{traceback.format_exc()}")
            error_text = f"Запуск не удался: {exc}"
            window.after(0, lambda: status.set(error_text))
            window.after(0, lambda: messagebox.showerror("Avelin", error_text))

    threading.Thread(target=start_worker, daemon=True).start()
    window.mainloop()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run_check() if args.check else run_gui())
