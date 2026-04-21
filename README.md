# Personal AI Agent

Личный AI-агент с веб-интерфейсом, API-слоем и архитектурой, которую позже можно упаковать в десктоп через Tauri.

## Структура

```text
AI_agent/
├─ backend/
├─ frontend/
├─ src/
│  └─ ai_agent/
├─ src-tauri/
├─ main.py
├─ requirements.txt
└─ .env.example
```

## Что уже есть

- CLI-режим агента для локальной отладки.
- Память на JSON.
- Инструменты и базовый tool calling.
- `mock` и `ollama` провайдеры.
- FastAPI API для веб-клиента.
- React/Vite каркас интерфейса.

## Backend

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn backend.main:app --reload
```

API поднимается на `http://127.0.0.1:8000`.

## Frontend

Для фронтенда нужен установленный Node.js.

```powershell
cd frontend
npm install
npm run dev
```

Vite dev server ожидается на `http://127.0.0.1:5173`.

## Почему это удобно для Tauri

- фронтенд уже отделен от backend;
- папка `src-tauri/` зарезервирована под будущую десктопную упаковку;
- UI будет переиспользован без переписывания логики чата;
- backend можно оставить HTTP-слоем или позже встроить глубже в Tauri.
