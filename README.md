# Avelin

Личный AI-агент с веб-интерфейсом, FastAPI backend и отдельным ядром агента в
`src/ai_agent/`. Проект подготовлен так, чтобы позже его можно было упаковать в
desktop-приложение через Tauri.

## Структура

```text
AI_agent/
├─ backend/        FastAPI API: /api/health, /api/bootstrap, /api/chat/stream
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

Быстрый dev-запуск backend и frontend одной командой:

```powershell
.\scripts\dev.ps1
```

Только backend или только frontend:

```powershell
.\scripts\dev.ps1 -Target backend
.\scripts\dev.ps1 -Target frontend
```

Если нужно заодно установить зависимости:

```powershell
.\scripts\dev.ps1 -Install
```

Скрипт поднимает backend на `http://127.0.0.1:8000`, frontend на
`http://127.0.0.1:5173`. Backend и frontend открываются в отдельных PowerShell-окнах;
остановить сервер можно через `Ctrl+C` в его окне.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn backend.main:app --reload
```

API поднимается на `http://127.0.0.1:8000`.

Данные агента хранятся в SQLite-файле `data/avelin.sqlite3` (`DATABASE_FILE` в `.env`).
При первом запуске новая БД создаёт таблицы `users`, `sessions`, `auth_accounts`,
`chat_threads`, `messages`, `notes`, `model_settings` и импортирует старую память из
`data/memory.json`, если БД ещё пустая.

Логи backend пишутся в `.logs/backend.log` и дублируются в консоль. В логах видны метод,
путь, HTTP-статус и длительность каждого запроса; необработанные ошибки сохраняются с
traceback.

Быстрая проверка API:

```powershell
python -c "from fastapi.testclient import TestClient; from backend.main import app; from uuid import uuid4; c=TestClient(app); suffix=uuid4().hex[:8]; r=c.post('/api/auth/register', json={'email':f'smoke-{suffix}@example.com','username':f'smoke-{suffix}','password':'password123','display_name':'Smoke User'}); token=r.json()['token']; h={'Authorization':'Bearer '+token}; print(c.get('/api/health').json()); print(c.get('/api/bootstrap', headers=h).status_code); print(c.post('/api/chat', headers=h, json={'message':'hello'}).status_code)"
```

Основные chat-маршруты требуют Bearer-токен. Auth API:

- `POST /api/auth/register` - email, username, password, display_name.
- `POST /api/auth/login` - login и password.
- `GET /api/auth/me` - текущий профиль по Bearer-токену.
- `POST /api/auth/logout` - удаление текущей сессии.
- `GET /api/auth/oauth/google` и `/api/auth/oauth/vk` - подготовленные OAuth-контракты.
- `GET /api/models` - список доступных provider-ов и моделей.
- `GET /api/model-settings` и `PUT /api/model-settings` - чтение и смена активной модели пользователя.
- `POST /api/chat/stream` - поток NDJSON с событиями `delta`, `status`, `done` и `error`.
- `GET /api/chats/{thread_id}/context-trace` - последний воспроизводимый trace состава контекста.

Ответы ассистента сохраняются вместе со статусом (`pending`, `complete`, `cancelled`,
`failed`), provider-ом и моделью. Ошибки Ollama не записываются как обычные ответы и
не раскрывают тело ответа upstream-сервера.

## Ollama Cloud

Для облачного запуска положи тестовый ключ в игнорируемый Git-файл `API_KEY.txt`
(одна строка с ключом) либо задай `OLLAMA_API_KEY` в `.env`. При наличии ключа по
умолчанию используются `ollama_cloud` и `beam:cloud`. Значения можно переопределить:

```dotenv
MODEL_PROVIDER=ollama_cloud
MODEL_NAME=beam:cloud
OLLAMA_CLOUD_MODEL=beam:cloud
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

Интерфейс получает ответ потоково, поддерживает Markdown/GFM и позволяет остановить
генерацию. Частичный ответ после остановки остаётся в истории с соответствующим статусом.
Успешно созданные документы и другие файлы показываются под итоговым ответом Avelin
отдельными карточками со скачиванием. Служебные сообщения файловых инструментов в чат
не выводятся; старые записи истории с таким сообщением преобразуются при загрузке.
Avelin также может искать публичную информацию через `web_search` и читать известные
страницы через безопасный `http_request`. Доступ ограничен GET/HEAD, публичными адресами,
таймаутом и лимитом ответа; локальные и приватные сети по умолчанию заблокированы.
Поисковая выдача проходит фильтр релевантности: страницы входа, общие главные страницы,
дубли и результаты не из запрошенного каталога не используются как источники. Если для
исследовательского Word-файла найдено меньше двух подходящих источников, файл не создаётся.

LLM-провайдеры подключаются через общий registry/runtime-контракт с описанием capabilities,
едиными ошибками, потоковой очисткой при отмене и повтором временных ошибок до
`LLM_MAX_RETRIES`. Локальная Ollama, Ollama Cloud и mock не требуют изменений frontend.

## Context Engine

Перед каждым обращением к модели Context Engine собирает prompt в явный токен-бюджет:
системные инструкции, grounding-правила, дословные пользовательские запреты, текущий
проект и активную задачу нельзя вытеснить обычной историей. Оставшийся бюджет делится
между последними сообщениями, релевантной памятью, extractive-фрагментами старой истории
и результатами действий. Если закреплённые данные физически не помещаются, запрос
завершается ошибкой `context_overflow`, а не молча теряет ограничения.

Параметры:

```dotenv
CONTEXT_WINDOW_TOKENS=32768
CONTEXT_OUTPUT_RESERVE_TOKENS=4096
```

Подсчёт использует детерминированный профиль семейства модели. В trace сохраняются
бюджет, резерв вывода, токены tool schemas, включённые/пропущенные части и fingerprint.
Это позволяет воспроизвести, почему конкретные данные попали или не попали в prompt.

## Локальный лаунчер

Создать или обновить ярлык `Avelin Core Agent` на рабочем столе:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_launcher.ps1
```

Ярлык запускает GUI без консольных окон, проверяет backend/frontend, открывает браузер и
останавливает поднятые им процессы при закрытии. Логи запуска находятся в
`.local/logs/`. Диагностическая проверка без GUI:

```powershell
.\.venv\Scripts\python.exe .\scripts\launch_local.py --check
```

Регрессионные тесты:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
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

## GitHub-стратегия

Avelin развивается в двух независимых линиях:

- `app` - desktop-приложение агента: React/Vite UI, FastAPI backend, `src/ai_agent/`, Tauri-интеграция, локальные данные, инструменты и релизы приложения.
- `website` - публичный сайт: landing, новости, страницы загрузки, публичные release notes и hosting-specific код.

PR для приложения открываются в `app`, PR для сайта - в `website`. Общие бренд-материалы и документация синхронизируются осознанно небольшими изменениями. Подробные правила лежат в [docs/github-strategy.md](docs/github-strategy.md).
