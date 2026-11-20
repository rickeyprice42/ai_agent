# AVE-75: killable research HTTP/parser phase

On Linux, `research_read` now runs its public HTTP fetch, decoding, HTML/JSON
parser, site adapters and structured evidence construction inside a fresh fixed
trusted broker process. This covers a stuck parser as well as blocking network
operations. Registry ownership and the broker's PID namespace teardown are
preserved. No arbitrary network permission was added to generic workers.

The phase has an empty read-only workspace and no provider credentials, live
clients, database, user files or browser profile. The existing public transport
validates redirects and pins DNS addresses. The broker shares host networking;
its trusted transport enforces destinations, not a kernel destination firewall.

The parent owns the cache and trace. Completed static reads keep the original
fields, evidence, safety markers and result schema. Dynamic/TLS browser fallback
receives only necessary fallback metadata, without a second HTTP fetch or raw
hydration payload. Forced-browser requests skip this phase. A host failure or
cancel does not launch browser fallback or cache a partial result, and cancellation
is checked at the handoff. Login/policy/anti-bot refusals stay typed failures.

## Verification

- Ubuntu as `avelin`, `setpriv --no-new-privs`, using the installed Chromium path:
  **186 tests, 185 passed, one expected platform skip**. Covers research/browser
  parsing and catalog contracts, search/HTTP brokers, process host, tool/run
  cancellation, runtime and answer presentation, long tasks and automations.
  Log: `/tmp/ave75-research-regression.log`.
- New real-process tests exercise production registration, parent cache retention,
  dynamic/TLS fallback and anti-bot refusal. A deliberately non-cooperative parser
  creates a detached network descendant; explicit cancel and deadline both tear
  it down before terminal status, with no delayed network send, no browser call
  and no partial cache entry. Private metadata targets fail closed.
- Portable tests compare extraction contracts across the existing public-page
  fixtures and check host failures, cancellation during handoff, forced-browser
  bypass and unsupported private profiles.
- Real HTTPS smoke under the same service user/NoNewPrivileges: `example.com`
  returned HTTP 200 through the fixed process, three evidence fragments and a
  parent cache hit on repeat. No model was involved.
- Windows final regression: **129 tests, 100 passed, 29 expected Linux-only
  skips**. Log: `.local/ave75-research-final-windows.log`. Windows retains
  cooperative execution; Linux-only teardown tests do not establish a Windows
  hard-kill guarantee. Exact-release checks are recorded in the Linear receipt.
- Paid model/API calls: **0**. Fixture transports and responses cover semantics;
  this is not a live model answer-quality or cost benchmark. Production accounts,
  chats and files were not used as test fixtures.

## Remaining scope

This migration bounds only the HTTP/parser phase. Browser execution and its DOM
processing, stateful and connector handlers still require separate profiles.
Existing drain/ownership semantics remain for them; AVE-75 stays In Progress.
Already accepted remote requests cannot be rolled back. A killed phase loses
its uncommitted result, and the IPC limit rejects excessive output rather than
silently claiming complete extraction.
