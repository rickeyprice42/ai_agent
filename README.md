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
- `GET /api/tools`, `/api/tools/{name}` и `/api/tools/capabilities` - discovery production-контрактов инструментов.
- `GET /api/model-settings` и `PUT /api/model-settings` - чтение и смена активной модели пользователя.
- `POST /api/chat/stream` - поток NDJSON с событиями `delta`, `status`, `done` и `error`.
- `GET /api/chats/{thread_id}/context-trace` - последний воспроизводимый trace состава контекста.
- `GET /api/chats/{thread_id}/compactions` - список циклов сжатия и их статусы.
- `GET /api/chats/{thread_id}/compactions/{id}` - summary вместе с исходными сообщениями для проверки.
- `POST /api/chats/{thread_id}/compactions/{id}/verify` - подтвердить summary для использования в контексте.
- `POST /api/chats/{thread_id}/compactions/{id}/rollback` - исключить summary и вернуться к оригиналам.
- `GET/POST /api/memory/records` - просмотр и сохранение персональной памяти с явным согласием.
- `PATCH/DELETE /api/memory/records/{id}` - изменение, отзыв и удаление записи владельцем.
- `GET /api/chats/{thread_id}/memory-retrieval-trace` - последний замер поиска памяти: кандидаты, источники, токены, latency и конфликты.
- `GET/PUT /api/memory/settings` - opt-in-настройка автономной памяти; по умолчанию она выключена.
- `GET /api/memory/summary` и `POST /api/memory/summary/rebuild` - просмотр и пересборка редактируемой сводки памяти.
- `GET /api/chats/{thread_id}/memory-extraction-trace` - обезличенные метрики последнего цикла извлечения без содержимого сообщений.
- `GET /api/memory/export?include_sensitive=false` - экспорт; Health-данные по умолчанию исключены.

Ответы ассистента сохраняются вместе со статусом (`pending`, `complete`, `cancelled`,
`failed`), provider-ом и моделью. Ошибки Ollama не записываются как обычные ответы и
не раскрывают тело ответа upstream-сервера.

## Облачные модели и резервирование

Для облачного запуска положи тестовый ключ в игнорируемый Git-файл `API_KEY.txt`
либо задай ключи в `.env`. Поддерживается старый формат из одной строки с ключом
Ollama и именованный формат для нескольких провайдеров:

```text
Avelin Ollama Cloud API:
<key>

Avelin OpenAI API:
<key>
```

При наличии обоих ключей `beam:cloud` используется как основная reasoning-модель,
а `gpt-6-luna` — как резервная. Для изображений основной маршрут использует
`gemma4:31b`, резервный — `gpt-6-luna`. Переключение reasoning происходит только
до начала выдачи ответа, чтобы пользователь не получил смесь двух ответов.
Изображение отправляется облачной модели только после отдельного approval.
Значения можно переопределить:

```dotenv
MODEL_PROVIDER=ollama_cloud
MODEL_NAME=beam:cloud
OLLAMA_CLOUD_MODEL=beam:cloud
OPENAI_MODEL=gpt-6-luna
REASONING_FALLBACK_ENABLED=true
VISION_PRIMARY_MODEL=gemma4:31b
VISION_FALLBACK_MODEL=gpt-6-luna
```

Проверка всех четырёх маршрутов без вывода ключей:

```powershell
.venv\Scripts\python.exe scripts\smoke_multimodal.py
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
Для динамических страниц доступны отдельные Playwright-инструменты `browser_read` и
`browser_interact`. Runner запускает установленный Microsoft Edge в изолированном
одноразовом профиле, сначала возвращает текст и интерактивные элементы DOM, а screenshot
сохраняет как скачиваемый artifact. Каждый запуск ограничен исходным host (либо
`BROWSER_ALLOWED_HOSTS`), private/local сети и загрузки заблокированы. Чтение разрешено
policy автоматически, а клики и другие взаимодействия требуют точного approval;
устаревший `expected_dom_hash` останавливает действие до клика.
Для исследовательского чтения используется `research_read`: сначала bounded HTTP с
извлечением `main`/`article`, таблиц, списков и JSON-LD, а browser fallback запускается
только для JavaScript-страницы. Результат содержит provenance, region/locale,
confidence и хэши цитируемых фрагментов. Login, paywall, anti-bot, private network,
downloads и неподтверждённая каталожная доступность завершаются typed failure.
Актуальные ограничения перечислены в [support matrix](docs/research-support-matrix.md).
Поисковая выдача проходит фильтр релевантности: страницы входа, общие главные страницы,
дубли и результаты не из запрошенного каталога не используются как источники. Если для
исследовательского Word-файла найдено меньше двух подходящих источников, файл не создаётся.
Для Apple Books используются официальный каталог, storefront, цена и жанровые признаки;
для Apple TV карточка принимается только с прямой ссылкой `tv.apple.com` и явным
подтверждением Apple TV+, когда запрошена подписка. Если каталог или доступность нельзя
подтвердить, Avelin завершает исследование понятной диагностикой без выдуманного файла.
Повторный запрос не перезаписывает готовый документ: новый файл получает суффикс `_2`,
`_3` и так далее.

LLM-провайдеры подключаются через общий registry/runtime-контракт с описанием capabilities,
едиными ошибками, потоковой очисткой при отмене и повтором временных ошибок до
`LLM_MAX_RETRIES`. Локальная Ollama, Ollama Cloud и mock не требуют изменений frontend.

Настройки browser runner в `.env`:

```dotenv
BROWSER_TIMEOUT_SECONDS=20
# Необязательно: фиксированный allowlist через запятую. Без него каждый запуск
# ограничивается host исходного URL и его поддоменами.
BROWSER_ALLOWED_HOSTS=example.com,docs.example.com
```

Для управления обычными Windows-приложениями доступны `desktop_snapshot` и
`desktop_interact`. Runner сначала использует UI Automation и стабильные ссылки
на найденные контролы; координатный PyAutoGUI fallback по умолчанию выключен.
Работа разрешена только с окнами из точного allowlist, клики требуют approval,
а окна входа, паролей, банков и кошельков блокируются. Перед каждым действием
проверяются emergency stop и hash предыдущего скриншота. Ввод текста и паролей
в MVP намеренно не поддерживается.

```dotenv
DESKTOP_TIMEOUT_SECONDS=15
DESKTOP_ALLOWED_WINDOW_TITLES=Avelin Test GUI
DESKTOP_ALLOW_PYAUTOGUI=false
```

## Tool Registry

Каждый built-in регистрируется через версионируемый manifest: SemVer, описание,
строгие input/output JSON Schema, capabilities, permissions, timeout, retry policy и
признак idempotency. Объектные схемы по умолчанию закрыты через
`additionalProperties=false`; nested objects, arrays, enum, строковые и числовые
ограничения проверяются до вызова handler.

Orchestrator получает единый `ToolResult` со status, typed error и событиями выполнения,
поэтому success/failure не определяется разбором пользовательского текста. Registry
поддерживает timeout, cooperative cancellation, retry только для явно retryable ошибок и
защищённый idempotency cache. Старые строковые built-ins подключены через ограниченный
compatibility adapter. Structured-логи содержат только имя, версию, статус, попытки и
latency; content, заметки, ключи и токены редактируются до сохранения action log.

## Security Policy и approvals

Центральный policy layer классифицирует каждый вызов инструмента как `read`, `write`,
`external` или `high`. Чтение и создание новых файлов внутри workspace разрешены
автоматически; перезапись, запуск процессов и внешние side effects требуют отдельного
подтверждения с точными аргументами, scope и TTL. Изменение аргументов, отказ или
истечение подтверждения всегда создают новый запрос и не исполняют исходное действие.

Панель Security показывает ожидающие approvals и активные ограниченные grants. Кнопка
Emergency stop немедленно блокирует инструменты и отзывает все незавершённые approvals
и grants. Внешний web-контент маркируется как недоверенные данные, а SSRF, traversal,
symlink escape, shell chaining и попытки передать секреты во внешние инструменты
блокируются до handler. Дочерний процесс получает минимальный allowlist окружения без
ключей и токенов; это локальный isolation prototype, а production-контейнеризация и
OS-level network namespace остаются отдельным deployment layer.

API управления: `GET /api/approvals`, `POST /api/approvals/{id}/decision`,
`GET /api/security`, `PUT /api/security/emergency-stop`, `GET/POST
/api/permission-grants` и `DELETE /api/permission-grants/{id}`. Решения, emergency stop
и grants записываются в redacted action log и изолированы по пользователю.

## Coding tool pack

Поверх production Tool Registry доступны отдельные безопасные операции для работы с
кодом. Файловый слой поддерживает построчное чтение, SHA-256 metadata, Unicode-поиск,
точечный `exact edit` с обязательным precondition hash, move и recoverable delete/restore.
Если файл изменился после чтения, операция возвращает `stale_file` и не перезаписывает
пользовательские изменения.

Project inspection не запускает код. Тесты, lint, typecheck и build выполняются только
через предопределённые runners с workspace cwd, очищенным окружением, timeout и
ограниченным выводом; частичный вывод сохраняется при timeout. Git read-only инструменты
покрывают status, diff, log, branches и ref inspection. `stage`, `commit` и создание
локальной ветки требуют exact approval; автоматического `push` в tool pack нет.

## Durable task execution

Каждая задача получает отдельный `task_run` со стабильными состояниями `queued`,
`running`, `waiting_approval`, `paused`, `failed`, `completed` и `cancelled`. Worker
захватывает run атомарно, обновляет heartbeat и lease; просроченный lease возвращается
в очередь после перезапуска процесса. До и после tool-call сохраняется checkpoint с
версией плана, redacted-вызовом, observation, artifacts, evidence и следующим действием.

Deduplication key не позволяет повторить уже подтверждённый шаг. Если внешний или
high-risk вызов мог завершиться прямо перед сбоем, Avelin не запускает его повторно без
ручной проверки. Бюджеты ограничивают шаги, ошибки, время, токены и стоимость. Очередью
можно управлять через `GET /api/task-runs`, checkpoint API и действия
`POST /api/tasks/{task_id}/pause|resume|cancel`.

## Approvals, notifications и evidence reports

Approval-карточка показывает точный tool-call, redacted arguments, risk, scope и срок.
Одноразовый approve продолжает только связанный step; deny фиксируется в checkpoint и
оставляет задачу на паузе без скрытого выполнения. Если пользователь не отвечает,
`waiting_approval` не возвращается в очередь. Истёкшее разрешение нельзя использовать:
после явного resume создаётся новый approval с теми же проверяемыми аргументами.

Run- и daily-отчёты строятся детерминированно из checkpoints и typed evidence, без
LLM-пересказа. Они содержат подтверждённые действия, тесты/проверки, commits, ссылки на
workspace artifacts, блокеры, budget usage/limits и следующий шаг. API:
`POST /api/task-runs/{run_id}/report`, `GET /api/task-reports`,
`POST /api/task-reports/daily`, `GET /api/notifications` и
`POST /api/notifications/{id}/read`. Внешние каналы в этот контур не входят и могут
подключаться отдельно только после явного согласия пользователя.

## Avelin Automations

One-shot и weekly-автоматизации хранят timezone, human-readable правило, следующий
запуск и отдельный budget. Фоновый scheduler раз в 30 секунд только ставит обычный
durable run в очередь: модель и инструменты выполняются существующим worker-контуром.
Каждый scheduled slot имеет уникальную запись, поэтому рестарт и параллельный tick не
создают дубликаты. После offline-периода weekly schedule coalesce-ится в один запуск,
а следующее время вычисляется уже в будущем — без шторма накопившихся задач.

Управление доступно через `GET/POST /api/automations`,
`PUT /api/automations/{id}`, `POST /api/automations/{id}/pause|resume` и
`DELETE /api/automations/{id}`. В prompt можно использовать `{date}` и `{datetime}`
для уникальных имён локальных черновиков. Внешние действия по-прежнему проходят
центральную approval policy.

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
CONTEXT_COMPACTION_THRESHOLD=0.75
CONTEXT_COMPACTION_KEEP_RECENT=12
```

Подсчёт использует детерминированный профиль семейства модели. В trace сохраняются
бюджет, резерв вывода, токены tool schemas, включённые/пропущенные части и fingerprint.
Это позволяет воспроизвести, почему конкретные данные попали или не попали в prompt.

Когда полная история достигает `CONTEXT_COMPACTION_THRESHOLD` от входного бюджета,
Context Engine создаёт детерминированный extractive-снимок: цели, решения, факты и
открытые вопросы. Каждый пункт содержит `message:<id>`, а созданные файлы сохраняются
как `file:<path>`. Снимок имеет статус `pending_review` и не считается подтверждённым,
пока пользователь не вызовет `verify`. Одновременно может ожидать проверки только один
цикл. `rollback` исключает снимок из prompt, но не удаляет исходные сообщения или файлы.
Получить оригиналы для ручной сверки можно через detail endpoint compaction.

## Персональная память

Память разделена на `profile`, `preference`, `episode` и `fact`, содержит scope,
confidence, status, TTL/timestamps и provenance. Сохранение через API невозможно без
`consent_granted=true`. Истёкшие и отозванные записи не возвращаются в активном списке.
Все CRUD-запросы дополнительно ограничены владельцем записи.

Автономная память включается пользователем отдельно и по умолчанию выключена. После
успешно завершённого ответа детерминированный конвейер ищет только устойчивые факты и
предпочтения: профильные сведения и явные исправления могут быть сохранены сразу,
остальные кандидаты требуют повторного подтверждения в разных сообщениях. Повторы
объединяются с provenance, а явное изменение предпочтения переводит предыдущую запись
в `superseded`. Эпизоды имеют TTL. Автоматические записи помечаются отдельно от ручных,
видны в сводке и доступны для обычного редактирования, отзыва, удаления и экспорта.

Пароли, ключи, токены, финансовые и медицинские сведения, а также содержимое файлов и
документов автоматически отклоняются. Health-память автономно не заполняется. В trace и
логи попадают только счётчики решений, latency и оценка токенов, но не текст сообщений.

Перед сборкой prompt активные записи выбираются через SQLite FTS5 и metadata-фильтры
scope (`global/project/chat`), затем ранжируются по лексической релевантности,
confidence, актуальности, provenance и близости scope. В контекст попадают идентификатор
источника и confidence; противоречащие записи помечаются как `CONFLICT`, а отсутствие
совпадений возвращает пустой результат без догадок. Последний retrieval trace хранит
число кандидатов и результатов, примерную стоимость в токенах и latency. Векторный поиск
не включён: к pgvector следует переходить только после сравнения на измеримом benchmark.

Чувствительные Health-записи физически хранятся в отдельной таблице и не попадают в
обычное чтение или экспорт. Их просмотр и экспорт требуют отдельного явного действия в
интерфейсе. Тела запросов и содержимое памяти не записываются в backend-логи.

Локальный desktop runtime остаётся на SQLite. Эквивалентный production-контракт
PostgreSQL с row-level security находится в
`migrations/postgresql/0001_personal_memory.sql`.

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
