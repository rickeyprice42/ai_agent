# Avelin

Личный AI-агент с веб-интерфейсом, FastAPI backend и отдельным ядром агента в
`src/ai_agent/`. Проект подготовлен так, чтобы позже его можно было упаковать в
desktop-приложение через Tauri.

## Структура

```text
AI_agent/
├─ backend/        FastAPI API: /api/health, /api/bootstrap, /api/chat
├─ frontend/       React/Vite интерфейс
├─ src/ai_agent/   ядро агента, LLM-провайдеры, память и инструменты
├─ src-tauri/      будущий Tauri-слой, сейчас только заглушка
├─ docs/           проектная документация, roadmap и логотипы
├─ main.py         CLI-запуск агента
├─ requirements.txt
└─ .env.example
```

Старый экспериментальный каталог `Nova AI-Agent/` удален. Актуальная рабочая
поверхность проекта: `frontend/`, `backend/`, `src/ai_agent/`, `docs/`.

## Backend

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn backend.main:app --reload
```

API поднимается на `http://127.0.0.1:8000`.

Логи backend пишутся в `.logs/backend.log` и дублируются в консоль. В логах видны метод,
путь, HTTP-статус и длительность каждого запроса; необработанные ошибки сохраняются с
traceback.

Быстрая проверка API:

```powershell
python -c "from fastapi.testclient import TestClient; from backend.main import app; c=TestClient(app); print(c.get('/api/health').json()); print(c.get('/api/bootstrap').status_code); print(c.post('/api/chat', json={'message':'hello'}).json())"
```

## Frontend

```powershell
cd frontend
npm install
npm run dev
```

Vite dev server ожидается на `http://127.0.0.1:5173`.

Для production-проверки:

```powershell
cd frontend
npm run build
```

## Desktop environment

Окружение для следующего Tauri-этапа подготовлено:

- Node.js `v24.15.0`, npm `11.12.1`.
- Python `3.13.9`, `pip check` без конфликтов.
- Rust toolchain `stable-x86_64-pc-windows-msvc`: `rustc 1.95.0`, `cargo 1.95.0`.
- Visual Studio Build Tools 2022 с MSVC toolchain установлен.
- Microsoft Edge WebView2 Runtime найден: `147.0.3912.72`.
- Tauri CLI добавлен в `frontend` как dev-зависимость: `tauri-cli 2.10.1`.

Проверки:

```powershell
node --version
npm --version
python --version
pip check
rustc --version
cargo --version

cd frontend
npm audit --audit-level=moderate
npm run tauri -- --version
npm run build
```

## Roadmap

Основной план работ лежит в [docs/roadmap.txt](docs/roadmap.txt).
