# AVE-75 — isolated Python handlers and atomic outputs

## Changes

The new JSON-only `ProcessHandlerSpec` / `HostedHandler` reconstructs a trusted
importable handler in a fresh Linux interpreter, with bubblewrap filesystem,
network and PID isolation. No live Agent/client/lock, pickle or inherited secret
environment crosses the boundary. The worker has 1 GiB address-space and CPU-time
limits; IPC is bounded to 2 MiB and isolated from incidental stdout/stderr.

Fifteen local file, Office, image and document-writing tools now use this host on
Ubuntu. Read tools receive a read-only workspace; writable profiles require an
explicit parent permission. Long tasks mount only their job directory at the
original logical prefix, preserving result paths and hiding sibling jobs.

Text/DOCX/PPTX/image/Office-copy writers stage and atomically publish outputs.
Failed validation/build preserves the original; overwrite=False cannot clobber
a file created concurrently. Hard-killed staging files may remain hidden, without
being published as valid outputs. Already published changes are not rolled back.

Unknown writable-tool outcomes stop the Agent batch and subsequent model calls.
Timeouts never automatically retry mutations merely because they are labelled
idempotent. Unknown checkpoints remain interrupted/unverified. Approved-action
failure is no longer labelled completed or passed to synthesis as already done.

## Verification

- Ubuntu acceptance run: **252 tests, 249 passed, 3 platform skips**.
  `/tmp/ave75-host-acceptance.log`.
- Final approved-action/runtime/host-contract/atomic subset: **44 tests,
  43 passed, 1 platform skip**. Includes one additional approved-action regression:
  combined unique selected coverage **253 tests, 250 passed, 3 skips**.
  `/tmp/ave75-host-final-approval.log`.
- Actual processes verified cancellation/timeout of an ignoring handler, detached
  child teardown, absence of a delayed write, cleanup of descendants on normal
  return, and continued service usability after durable cross-connection cancel.
- OS boundaries verified filesystem/network/environment separation, read-only
  enforcement, per-job mounts hiding sibling files, and no writable import roots.
- Contract tests verified Unicode/large JSON, structured results, noisy stdout,
  redacted exceptions, malformed typed output, memory failure and subsequent calls.
- Actual hosted writers created/read text, valid DOCX, appended DOCX and valid PPTX.
  Forced worker termination preserved an existing target rather than publishing
  its partial replacement. No-clobber and failed DOCX/PPTX build tests passed.
- Parent policy denial prevented process startup. Mutation timeout remained one
  attempt; streaming/non-streaming Agent tests prevented a second batch action or
  model round. Approved external-action failure remained interrupted and unreplayable.
- Windows checks: 103-test focused set passed with 16 Linux-only skips before the
  final writer subset; the final 44-test portable subset passed without skips.
  Three atomic-output tests also passed. Explicit host calls on Windows fail closed.

An earlier image-detail fixture depended on local provider credentials; its
synthetic availability is now explicit. Providers and external effects in these
tests were scripted/mocked; no paid model API was called. Production accounts,
chats and files were not changed by the tests. These checks do not establish real
model task quality, physical phone acceptance or universal arbitrary-code safety.

## Remaining AVE-75 scope

Network/stateful/connector callbacks still need explicit reconstructable profiles
and validated egress. They retain cooperative cancellation and lease/drain behavior;
they are not silently forked or granted unrestricted network/client state. The new
host is therefore an implemented local-tool execution path, not a claim that every
legacy callback is hard-terminable. AVE-75 remains In Progress for that migration.
