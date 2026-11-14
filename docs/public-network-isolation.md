# Public HTTP and browser connections

`HttpSandbox` uses custom urllib HTTP/HTTPS handlers. Each connection resolves
the destination, rejects the whole answer if any address is non-public, then
connects the socket directly to one of the validated numeric endpoints. TLS
certificate verification and SNI retain the original hostname. Redirects use the
same handlers and URL policy. Environment proxy settings are not used here.

Each ephemeral Playwright browser uses its own authenticated loopback proxy.
The proxy applies the public-address check at every outgoing connection,
including HTTPS CONNECT tunnels. Chromium retains TLS verification. The existing
operation-host allowlist and page routing still apply. Proxy bypass for loopback
is disabled, QUIC is disabled and WebRTC non-proxied UDP is disabled. Service
workers remain blocked. The proxy supports HTTP(S) and uses bounded connections
and lifetimes. It strips proxy credentials before forwarding HTTP to the origin.

`allow_private_networks=True` is an explicit constructor override for controlled
local fixtures. Public deployments must keep it disabled. This transport does not
replace the OS sandbox used for generated code or an external egress firewall.

Regression tests are in `tests/test_public_transport.py`. They cover numeric
connection pinning, mixed/private/mapped-IP DNS answers, rebinding at HTTP and
browser entry points, proxy authentication and normal HTTP requests. Existing
browser/research suites cover page interactions and local fixtures. Real HTTPS
checks should run with the deployment's trusted CA configuration.
