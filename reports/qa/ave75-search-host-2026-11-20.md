# AVE-75: isolated public web search

Linux `web_search` now runs in a fresh, killable trusted transport process using
the existing HTTP broker lifecycle. Primary provider selection, result ranking,
citation parsing and optional OpenAI fallback remain in the existing search code.
Completed calls return bounded provider cooldown state to the parent.

Primary search and Apple requests now use URL/redirect validation and DNS-pinned
public connections. The fallback only accepts the official HTTPS OpenAI Responses
endpoint, rejects redirects and disables environment proxies. Its required key
travels through private stdin configuration, never through model arguments,
environment, argv or output. No live clients, user files or database are passed.
The broker shares host networking: destination control is enforced by trusted
transport code, not a kernel firewall. Generic process workers remain offline.

Registry cancellation/deadline handling retains unknown billable outcomes even
for read-only tools. Such outcomes cannot be automatically retried or followed
by another model/tool round. They indicate uncertainty, not a confirmed charge;
an already accepted remote request cannot be rolled back.

## Evidence

- Windows expanded regression: 124 tests, 99 passed, 25 expected Linux-only skips.
  Log: `.local/ave75-search-final-windows.log`.
- Ubuntu expanded regression as `avelin`: 179 tests, 176 passed, one expected
  platform skip, two fixture failures. Log: `/tmp/ave75-search-regression.log`.
  Both failures were corrected at test boundaries: the process roundtrip fixture
  needed its query schema; the HTTP error presentation fixture needed to replace
  the registered process handler rather than its inaccessible parent object.
- Final affected suites on Ubuntu with `setpriv --no-new-privs`: 22/22 passed
  (`tests.test_chat_baseline`, `tests.test_hosted_web_search`). Log:
  `/tmp/ave75-search-final-subset.log`. This includes real fresh bubblewrap search
  processes, primary and fallback fixtures, cooldown persistence, registry
  roundtrip, key non-disclosure, rebinding/redirect rejection and actual Agent
  sync/stream termination after uncertain paid-search outcomes.
- Final Windows baseline rerun: 12/12 passed. Log:
  `.local/ave75-search-baseline-final.log`.
- Earlier focused Ubuntu transport/search checks: 22 tests, 21 passed, one
  browser-path skip. Existing broker tests cover cancellation/deadline teardown
  including detached descendants. Log: `/tmp/ave75-search-focused.log`.
- Paid model/search API calls: **0**. Search transports and provider usage were
  synthetic fixtures; this does not accept real paid-search answer quality or
  establish measured cost savings. One earlier HTTP presentation fixture reached
  public example.com before its boundary was corrected; it was not a model call.

## Limits

Windows search remains cooperative. A forcibly killed process may lose an
in-flight cooldown update. Fallback credentials must exist in the fixed trusted
worker while it calls the provider; arbitrary callbacks/private-network profiles
are rejected. This change does not implement full billing/cache accounting.
Research, browser, stateful and connector profiles remain outstanding, so AVE-75
must stay In Progress. Production data are not test fixtures; no accounts, chats,
memory or files were modified by these tests.
