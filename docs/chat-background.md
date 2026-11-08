# Generation while the app is in the background

The web client starts a server-owned chat run with `POST /api/chat/runs`. The response is a durable run descriptor, not the lifetime of generation. A separate worker consumes the existing Agent stream and persists numbered events in SQLite. Closing an HTTP subscription or backgrounding the PWA only disconnects that reader.

The client supplies a unique `request_id`. Repeating the same ID and payload returns the existing run; a changed payload conflicts. Only one producer owns a thread. Legacy streaming and direct chat requests respect that reservation. Up to eight producers can run on the server concurrently.

The authenticated API supports:

- `GET /api/chat/runs/{run_id}`: current text, exact event cursor, state, message IDs, files and approvals.
- `GET /api/chat/runs/by-request/{request_id}`: recover an accepted request after losing its POST response.
- `GET /api/chats/{thread_id}/runs/active`: latest run, including its terminal result.
- `GET /api/chat/runs/{run_id}/events?after=N`: replay events after the cursor, then subscribe to new ones. Disconnecting this GET never stops generation.
- `POST /api/chat/runs/{run_id}/cancel`: explicitly request cancellation. A provider/tool call already in progress must return or time out before the worker can stop.
- `POST /api/chats/{thread_id}/approvals/{approval_id}/runs`: start a detached continuation for one explicit approval/denial, using the same idempotency and recovery contract.

Snapshots contain `run_id`, `request_id`, `thread_id`, `kind`, `state`, `outcome`, `text`, `latest_seq`, `message_id`, `user_message_id`, `user_text`, `attachments`, `artifacts`, `approvals`, `error`, and `cancel_requested`. Text and cursor are read from the same committed state. Events start at sequence 1 and finish with a `done` event containing the terminal state and outcome. Thread/account access is checked for status, replay and cancellation.

Completion notification and cancellation have an atomic ordering: an accepted cancel suppresses success notification; once a verified completion claims the run, a later cancel cannot relabel it cancelled. Notifications continue to use the existing persisted message ID deduplication. Denial, provider failure, partial generation and interrupted runs do not create a success notice.

Workers heartbeat only their live producer leases. Shutdown or an expired owner marks a run `interrupted`, preserves its partial result and does not replay unknown tool effects. A new message is required to continue after a server interruption. This supports backgrounding/reconnection while the server stays running; it does not promise crash-resume of arbitrary in-flight side effects.

The legacy `POST /api/chat/stream` contract remains available for old clients and CLI tests. Its connection-bound cancellation semantics are unchanged; the current web UI uses the detached API.

Verification: `tests.test_chat_runs` covers idempotency, ownership, approval continuation, both cancellation/completion races, provider failure, shutdown/replacement and queued cancellation. `tests.test_chat_background_network` uses a real isolated Uvicorn socket and a gated synthetic provider to prove HTTP disconnect/reload/replay and explicit cancellation. These are server/browser transport checks, not physical iPhone push delivery acceptance.
