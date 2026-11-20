# AVE-75: bounded browser and egress process pair

Linux `browser_read`, `browser_interact` and the browser fallback used by
`research_read` now use a fixed backend with two sibling bubblewrap processes.
The Chromium/Playwright/DOM worker has no external network namespace access.
Only a trusted proxy worker uses host networking, with public address validation,
per-operation host policy and DNS-pinned sockets. An authenticated Unix-socket
bridge plus an internal loopback relay joins them. Tool arguments cannot choose
entry points, mounts, proxy addresses or enable private networks.

The supervisor retains ownership and joins both process domains before returning.
Cancel/deadline and ordinary completion kill any remaining descendants, including
detached processes. Private scratch/profile state is separate from user files.
Provider keys, the application database and user workspace are not mounted or
inherited. Screenshots are copied through bounded JSON IPC and published by an
atomic parent writer after teardown and cancellation checks. Failed/cancelled
captures preserve an existing target. Unknown external interaction outcomes
remain unknown and are not automatically retried.

## Verification

- Ubuntu service user `avelin` with `setpriv --no-new-privs`: **201 tests, 200
  passed, one expected platform skip**. Includes actual Chromium, HTTP/research
  and search profiles, registry/process lifecycle, tool/run cancellation, answer
  presentation, long tasks and automations.
  Log: `/tmp/ave75-browser-regression.log`.
- Windows final regression: **138 tests, 104 passed, 34 expected Linux-only
  skips**. Log: `.local/ave75-browser-final-windows.log`.
- New process acceptance tests exercise real DOM reads/clicks, DOM-hash rejection,
  screenshot publication, metadata-target refusal, reader fallback through the
  registered backend and no user state/secret inheritance. A probe inside the
  browser worker cannot connect directly to a public IP or host-loopback listener,
  but the same worker successfully reads the synthetic page via its proxy.
- A stuck DOM snapshot creates a detached child while Chromium issues heartbeat
  requests. Both explicit cancellation and deadline drain the browser/proxy pair:
  no heartbeat or delayed descendant request appears after terminal status, and
  the old screenshot survives. A separate stuck-proxy startup test proves the
  same behavior before Chromium starts. Synthetic HTTP listeners and credentials
  belong only to fixtures, never production users.
- Real public HTTPS smoke: example.com returned HTTP 200 and a real PNG through
  the networkless Chromium profile under NoNewPrivileges. The screenshot was
  77,736 bytes with a content checksum. No model API was involved.
- A final acceptance assertion checks a public-page redirect toward a host-private
  listener; it is blocked before `/sentinel` is reached. Exact-release results and
  deployment/backup receipts are recorded in Linear.
- Paid model/search API requests: **0**. These checks cover lifecycle, transport
  and browser behavior; they do not certify model answer quality or cost savings.

## Limits and remaining work

Ubuntu AppArmor denied nested bubblewrap (`unpriv_bwrap` capability denial), so
the implementation uses sibling namespaces. No security policy was disabled,
no service capabilities added and NoNewPrivileges remains enabled. The proxy's
public destination checks are trusted application validation, not a destination
firewall. Chromium itself has a network namespace boundary against direct egress.

The browser worker keeps deadline, CPU and 2 MiB IPC limits but cannot use the
generic worker's 1 GiB RLIMIT_AS because Chromium reserves large virtual ranges.
Generic/file/HTTP workers retain their limit. This change does not provide a
dedicated Chromium resident-memory/cgroup quota. PNG output is capped at 900,000
bytes; larger screenshots fail explicitly without replacing an existing file.

Windows remains cooperative. Reader evidence processing after the browser returns,
stateful callbacks and connectors remain outside this migration. AVE-75 stays
In Progress; do not claim a universal hard-stop guarantee. Remote actions already
accepted by a site cannot be rolled back by cancelling local execution.
