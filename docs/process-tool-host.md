# Isolated Python tool host (AVE-75)

Use `HostedHandler(ProcessHandlerSpec(...))` for a trusted, importable Python
handler with the signature `run(arguments, configuration)`. Both inputs and the
result must fit the JSON contract. Return a `ToolResult` for explicit failures;
an ordinary JSON value is a successful raw result. The server owns the descriptor:
it is never a model-supplied import path, generated manifest or pickle payload.

The handler is imported in a fresh Python interpreter inside Linux bubblewrap.
Only the Python runtime, read-only code roots and the authorized workspace are
mounted. Network, host environment, other account data and writable import roots
are absent. The workspace is read-only by default. Writable profiles require a
declared write/high permission when registered. Existing policy checks and input
validation still happen in the parent before execution.

The child receives no live Agent, database connection, lock, token cache or client
object. There is no reuse of forked Python handler state. Long-task profiles bind
only the task directory at its original logical prefix, preserving tool paths
while hiding sibling jobs. Runtime read/write limits are snapshotted per call.

The parent polls cancellation/deadline and kills/drains the entire PID namespace.
Handlers cannot evade teardown by ignoring an Event or creating a new session.
Returning successfully also ends detached descendants: asynchronous work must not
outlive the tool call. The parent keeps ownership/leases until teardown completes.

The worker limits address space to 1 GiB and CPU time to its deadline allowance.
The private JSON channel is capped at 2 MiB in each direction. Incidental stdout,
stderr and inherited subprocess output go to `/dev/null`; the private protocol FD
is not inherited by exec'd children. Exceptions and protocol failures do not expose
worker stderr/configuration. Production file validators may expose their known
input errors; arbitrary module ValueErrors are redacted by default.

On Ubuntu, these 15 tools now use the host:

- `read_file`, `read_file_lines`, `read_pdf_pages`, `file_metadata`;
- `list_workspace_files`, `search_workspace`, `write_file`, `edit_file_exact`;
- `create_docx`, `append_docx`, `create_presentation`;
- `inspect_image`, `transform_image`, `inspect_office_document`, `edit_office_document`.

Text, DOCX/PPTX, image and Office-copy outputs use same-directory staging and
atomic publication. Validation/build failure leaves the previous target intact.
`overwrite=False` uses atomic no-clobber publication. A hard-killed worker can
leave a hidden `.pending` staging file; it is not listed/delivered as a result.
This is not rollback of a file already published or a remote action already sent.

A writable handler timeout, process/protocol failure or unclassified exception
is an unknown outcome. `validation_errors_safe=True` is reserved for known file
validators that reject before publication; it must not hide a failure after a
side effect. Unknown writes are never automatically retried, even if a tool claims
idempotency. Agent stops the current batch/model loop; the checkpoint is interrupted
and unverified. Approved actions stay interrupted rather than being marked done
or passed to another synthesis round as already completed.

Explicit host calls fail closed on unsupported platforms. Existing Windows file
tools retain their cooperative execution path and use the atomic writers; there
is no Windows hard-kill guarantee from this Linux host.

The remaining AVE-75 migration covers network/stateful/connector handlers, which
cannot safely inherit clients or gain unfiltered network access. They retain the
drain/ownership semantics until explicit reconstructable profiles and validated
egress are implemented. The host is not yet a universal execution contract for
every legacy callback. Code/Git/shell and generated pure-data tools keep their
existing process runners. Direct low-level sandbox calls outside ToolRegistry do
not acquire this host's lifecycle guarantees.


## Fixed public HTTP transport (16 November)

On Linux, `http_request` uses a separate, fixed trusted GET/HEAD broker. Generic
`ProcessHandlerSpec` profiles still have no network capability. The broker shares
the host network only to run the existing `HttpSandbox` transport: URL and header
validation, validation of each redirect, and DNS-address pinning on each socket.
This is a trusted-code egress boundary, not a kernel destination firewall or an
execution environment for arbitrary/generated network callbacks.

The broker mounts an empty read-only temporary workspace, code/runtime and only
DNS/NSS/public CA configuration. It does not inherit provider credentials, live
clients, the application database or user files. A model cannot select its import
entry point, enable private networks or change this configuration through tool
arguments. The private-network compatibility option is blocked for this profile.
Windows registration retains the existing cooperative HTTP implementation.

The registry deadline/cancel signal kills and drains the broker PID namespace,
including detached descendants, before terminal status. Blocking DNS or socket
work can therefore no longer keep this particular tool alive indefinitely. No
remote request already received by its server is rolled back. Research/search,
browser, stateful and connector callbacks still require their own profiles;
AVE-75 remains open for those migrations. See
`reports/qa/ave75-http-host-2026-11-16.md` for scoped evidence.

## Fixed web search transport (20 November)

Linux `web_search` now reconstructs its public providers in the same killable
trusted broker, with an empty read-only workspace. This is another fixed entry
point, not a network permission for generic workers. Search and Apple catalog
requests validate URLs/redirects and pin DNS addresses at socket creation.

When the OpenAI fallback is configured, only its required credential and scalar
configuration cross private JSON stdin. No live client, inherited environment,
application database or user files are passed. The fallback accepts only the
official HTTPS `/v1/responses` endpoint, rejects redirects, disables environment
proxies and pins public addresses. This is trusted transport validation, not a
kernel destination firewall.

Completed calls merge bounded provider cooldowns back into the parent so fresh
processes retain anti-bot pauses. A forced kill may lose an in-flight cooldown
update. A timeout or cancellation with possible billable fallback marks the
outcome and usage unknown: it is not automatically retried or followed by another
model/tool round. This cannot undo a request already accepted remotely and does
not prove that a charge occurred.

Windows keeps cooperative search execution. Research, browser, stateful and
connector callbacks still need their own process profiles; AVE-75 remains open.
See `reports/qa/ave75-search-host-2026-11-20.md` for scoped verification. No paid
provider requests were used to validate this migration.

## Research HTTP and parsing phase (20 November)

Linux `research_read` now runs public HTTP fetching, HTML/JSON parsing, site
adapters and evidence construction in a fresh fixed broker process. Its empty
read-only workspace has no application database, user files, browser profile or
provider keys. It uses the validated/pinned `HttpSandbox` transport. No generic
network capability was added; this remains trusted application egress validation.

The parent retains the reader cache and trace. A successful HTTP phase returns
the structured result. A page needing JavaScript or independent browser TLS
verification returns only fallback metadata, not the raw page/hydration payload.
The parent runs the existing browser fallback without repeating the HTTP fetch.
Explicit force-browser requests bypass the HTTP worker. Login, policy and anti-bot
failures retain their typed refusal behavior.

Host timeout, cancellation and protocol/output-limit failures stop the reader
without launching browser fallback or caching a partial result. Cancellation is
also checked between HTTP completion and browser startup. Linux tests exercise
a deliberately stuck parser and detached network descendant, not only a slow
socket. Windows keeps the cooperative implementation.

This bounds the HTTP/parser phase only. Browser execution and its subsequent
DOM parsing still use the existing cooperative/drain path; stateful and connector
profiles also remain outstanding. AVE-75 is not complete. Evidence is recorded in
`reports/qa/ave75-research-http-2026-11-20.md`.

## Browser process pair (21 November)

On Linux, `browser_read`, `browser_interact` and the browser used by
`research_read` now share a fixed process backend. A networkless bubblewrap
process runs Playwright, Chromium, DOM snapshots and catalog HTML extraction.
A separate fixed egress process uses host networking, validates the initial
URL/host policy and pins public addresses on every outgoing connection. The two
communicate through an authenticated HTTP proxy over a private Unix socket;
only a loopback relay exists inside the browser network namespace. Chromium
cannot bypass the proxy with a direct socket to public or host-loopback targets.

These are sibling namespaces: Ubuntu AppArmor rejected nested bubblewrap. No
host security policy, NoNewPrivileges setting or generic process capability was
relaxed. The parent holds ownership until both process domains are killed and
drained on completion, cancellation or deadline, including detached descendants.
It does not turn a remotely accepted browser action into a reversible one.
Uncertain interaction failures retain outcome_unknown and are not retried.

Neither process sees user files, the application database or provider keys. The
browser runtime, fonts and CA certificates are read-only; profiles/screenshots
are ephemeral. The proxy alone gets a writable private scratch bridge for its
socket and per-call credentials. Screenshots cross bounded JSON IPC and are
published atomically by the parent after process teardown and cancellation
checks. Existing files survive failed/cancelled captures. The PNG limit is
900,000 bytes; excessive output fails explicitly rather than silently truncating.

Browser presence is checked from the installed Playwright manifest/files without
starting a driver in the service process. The fixed browser worker retains CPU,
deadline and IPC limits, but omits the generic Python worker's 1 GiB RLIMIT_AS
because Chromium reserves a large virtual address range. This is not a dedicated
resident-memory/cgroup quota for Chromium; generic workers keep their old limit.

Windows remains cooperative. The reader's final post-browser evidence processing,
stateful and connector callbacks still need scoped migration; AVE-75 stays open.
See `reports/qa/ave75-browser-host-2026-11-21.md` for verification and limits.

## Offline browser evidence processing (23 November)

Linux `research_read` now also reconstructs its final browser-result processing
in a fixed, network-disabled Python worker: page refusals, catalog/offer checks,
structured fields, safety markers and evidence fragments. Only the bounded JSON
snapshot, normalized metadata and max-fragment scalar cross IPC. The worker has
an empty read-only workspace; no browser object, live provider, credentials,
database, user files or proxy bridge is passed. Generic memory/CPU/deadline/IPC
limits apply to this pure-data stage.

The parent retains cache/trace ownership. Cancellation is checked between browser
completion and evidence startup, and again after preparing a detached cache copy,
before publication. Host failures, excessive input/output and cancellation cannot
cache partial results or silently fall back to processing in the service thread.
The browser is not automatically rerun after evidence-processing failure. Windows
keeps the direct implementation. Cache keys/TTL, result schema, verified prices,
refusals and untrusted-source flags remain unchanged.

This completes the process profiles for the expensive research extraction phases,
not every callback in AVE-75. Bounded orchestration/IPC/cache bookkeeping remains
in the parent. Stateful callbacks and provider/connector clients need separate
profiles and outcome handling. See `reports/qa/ave75-evidence-host-2026-11-23.md`.
