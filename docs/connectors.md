# MCP и Connector слой Avelin

Connector слой подключает внешние сервисы к существующему Tool Registry, не
добавляя специальные ветки в orchestrator. Каждый обнаруженный внешний tool
получает versioned manifest, JSON Schema, capabilities, permissions, timeout и
обычную центральную policy-проверку.

## Контракт

- `ConnectorRegistry` отвечает за register, list, search, capabilities,
  enable/disable, resources и prompts.
- `McpClient` реализует lifecycle `initialize`, `tools/list`, `resources/list`,
  `prompts/list`, `tools/call`, disconnect и cancellation через transport-neutral
  request callback. Текущий клиент намеренно использует совместимый legacy
  handshake `2025-11-25`; modern era `2026-07-28` потребует отдельного
  `server/discover` transport режима.
- Transport владеет credentials. Токены не входят в MCP params, schemas,
  manifests, model context, public errors или connector discovery.
- Read-only tool имеет permission risk `read`. Write/send tool объявляется как
  `external` или `high` и проходит существующий одноразовый approval.
- Внешний результат всегда содержит provenance и `trust=untrusted_external_data`.
  Перед добавлением в model context он оборачивается в untrusted block; найденная
  prompt injection только помечается как данные и не исполняется.

Typed failures: `disconnected`, `auth_required`, `permission_denied`,
`rate_limited`, `unavailable`. Schema mismatch и timeout дополнительно
обрабатываются production-контрактом Tool Registry.

## API и UI

- `GET /api/connectors` — список или поиск через `?query=`;
- `PUT /api/connectors/{id}` с `{ "enabled": true|false }` — подключение или
  отключение;
- `GET /api/bootstrap` включает текущее состояние connectors.

Экран Connectors показывает connection status, scopes, capabilities, tools и
позволяет отключить подключение. Отключённые tools исчезают из model discovery.

## Подключение transport

Production transport создаёт `McpClient` и передаёт callback с сигнатурой
`(method, params, timeout_seconds, cancellation_event)`. Callback самостоятельно
добавляет credentials на сетевой/stdio границе и никогда не возвращает их в
результат. После `agent.connectors.register(client)` MCP tools становятся обычными
tools Avelin без изменения Agent, Planner или ExecutionEngine.

Contract tests используют два независимых in-memory источника: read-only catalog
и mail send. Они проверяют discovery, approval, schema mismatch, timeout, rate
limit, auth/disconnect, malicious output и отсутствие секрета в discovery/errors.
