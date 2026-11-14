# Process isolation

Code checks, Git processes and allowed shell commands use Linux bubblewrap
(`/usr/bin/bwrap`). Install the distribution package with `apt install bubblewrap`.
The service identity must be allowed to create the namespaces used by bubblewrap.
Check this using the actual unprivileged service user and systemd restrictions.
Unsupported systems or failed namespace creation reject execution. There is no
host-process fallback. Windows needs a separate isolated executor before code
execution is available there.

The job sees read-only `/usr`, system binary/library directories and its Python
runtime prefix. Only the calling workspace is mounted writable at `/workspace`.
Temporary storage, PID namespace, process environment and network namespace are
private. Provider credentials, account state, host home directories and service
configuration are outside the mount tree. Job approval continues to apply.

The runtime installation is trusted administrator-managed code. Keep it separate
from writable account workspaces. An editable Python installation pointing outside
the mounted runtime is unsupported. Dependencies needed by jobs must be installed
in that runtime by an administrator.

Run `python -m unittest tests.test_process_isolation` on Linux with bubblewrap.
The integration test runs a synthetic unittest, verifies ordinary file output,
and checks sibling/host paths, environment secrets and external network access.
Repeat in the deployed service context before releasing. AVE-75 and AVE-86 cover
process cancellation and resource/output quotas separately.
