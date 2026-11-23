# AVE-75: offline browser-evidence processing

Linux `research_read` now runs final browser snapshot processing in a fresh
network-disabled process: refusals, catalog/merchant offer validation, structured
fields, untrusted-source flags and evidence construction. This extends the earlier
HTTP/parser and browser process profiles without adding generic network access.

Only bounded JSON snapshot/metadata and the fragment limit cross IPC. The worker
uses an empty read-only workspace with generic memory/CPU/deadline/IPC limits;
no browser object, live provider, credentials, database, user files or proxy bridge
is passed. Host timeout/cancel kills its PID namespace and descendants before
returning. Excessive IPC fails explicitly, without an in-thread fallback.

The parent owns cache and trace. Cancellation is checked at the browser/evidence
handoff and after preparing the detached cache copy, before committing it. Failed
or cancelled processing does not repeat the browser or cache a partial result.
Cache key/TTL and output schema remain unchanged. Windows keeps direct execution.

## Verification

- Ubuntu as `avelin` with `setpriv --no-new-privs` and the installed browser path:
  **210 tests, 209 passed, one expected platform skip**. Includes evidence,
  research/catalog, real Chromium, search/HTTP profiles, process lifecycle,
  tool/run cancellation, runtime presentation, long tasks and automations.
  Log: `/tmp/ave75-evidence-regression.log`.
- Final Windows regression: **154 tests, 117 passed, 37 expected Linux-only
  skips**. Log: `.local/ave75-evidence-final-windows.log`.
- Initial focused Ubuntu acceptance: 45/45 passed. The later expanded suite also
  includes cancellation during cache-copy preparation. Log:
  `/tmp/ave75-evidence-focused.log`.
- Real fresh-worker tests verify no direct network connection, secret environment,
  provider-key file or application database. A deliberately stuck postprocessor
  creates a detached child; cancel and deadline both stop it before a delayed
  write, with no cache entry or repeated browser call. The test-only writable
  sentinel directory is not a production worker permission.
- Contract tests preserve exact product price/currency, normalized image URL,
  TLS-fallback metadata, evidence and untrusted flags. Login/paywall/anti-bot
  refusals remain typed. Registry integration preserves cache hits and does not
  reread the browser on repeat.
- Real HTTPS integration under the same user/NoNewPrivileges:
  `example.com` -> networkless Chromium -> offline evidence worker -> cache.
  HTTP200, browser extraction method, eight evidence fragments, repeat cache hit.
  No model was involved. This is transport/extraction evidence, not an assessment
  of model answer quality or measured cost savings.
- Paid model/search API requests: **0**. Tests use temporary data/fixtures and do
  not modify production accounts, chats, memory or files. Exact-release checks
  and deployment/backup receipts are recorded in Linear.

## Remaining scope

Expensive research extraction phases now have process profiles. Bounded parent
orchestration, JSON IPC and cache bookkeeping remain in the service. Stateful
callbacks and provider/connector clients still need explicit reconstructable
profiles and outcome semantics; AVE-75 remains In Progress. Generic guarantees
must not be inferred for every callback or for Windows. Cancelling local work
cannot roll back requests/actions already accepted by a remote service.
