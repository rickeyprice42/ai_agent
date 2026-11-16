# AVE-75: bounded public HTTP process profile

The Linux `http_request` tool now uses a fixed trusted GET/HEAD transport broker in a fresh Python process. The ordinary isolated host remains network-disabled; network access is not a new option on `ProcessHandlerSpec` and is not granted to generated callbacks, file tools or code runners.

The broker is the trusted egress boundary. It uses the existing `HttpSandbox` validation for schemes, credentials, headers and every redirect, plus `connect_public` which validates all resolved addresses and pins the actual socket connection to an approved address. The broker shares the host network; this is application-enforced destination validation in trusted transport code, not a kernel destination firewall. It must never load a caller-supplied module or serialized client.

Only the fixed entry point, request arguments and scalar runtime limits cross JSON IPC. Its workspace is a fresh empty read-only temporary directory. Provider credentials, the application database and user files are not mounted or inherited. The only added system configuration is read-only DNS/NSS and public CA certificates. The private-network compatibility option fails closed for this Linux profile.

The existing parent deadline and cancellation kill/drain the broker's PID namespace, including detached descendants. Ownership remains held until teardown. This bounds DNS resolution and blocking socket/response work too. It does not undo requests already received by a remote server.

Windows keeps its existing cooperative HTTP path. Other network, research, browser, stateful and connector callbacks have not been migrated by this change; AVE-75 remains In Progress.

## Verification

- Linux focused acceptance under `runuser -u avelin` and `setpriv --no-new-privs`: 7/7 tests passed. Real fresh interpreters/bubblewrap, private/mapped/metadata targets, forbidden headers/methods, ignored descriptor-injection arguments, DNS rebinding, host-state/secret isolation, cancellation and timeout of a detached network descendant. No delayed network send occurred after terminal status.
- Expanded Ubuntu regression: 130 tests run, 129 passed, 1 expected Windows-only skip. Includes process host/isolation, public transport, registry, tool/run cancellation, recovery, automations, long tasks and runtime audit. Log: `/tmp/ave75-http-regression.log`.
- Windows regression: 98 tests run, 74 passed, 24 expected Linux-only skips. Log: `.local/ave75-http-windows-regression.log`. Windows HTTP keeps the existing cooperative implementation.
- Actual HTTPS smoke through ToolRegistry and the fixed broker, running as avelin with NoNewPrivileges: `https://example.com` returned HTTP 200 with bounded content and normal TLS certificate validation. No user workspace or credentials were supplied.
- Loopback listeners and temporary files/databases belong only to fixtures. The test-only broker fixture is never registered as a product tool. Production accounts, chats and files were not changed by these tests.
- Paid model API calls: **0**. These checks establish the scoped transport/lifecycle behavior, not model answer quality or complete AVE-75 coverage.

