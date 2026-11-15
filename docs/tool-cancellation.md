# AVE-75 — cancellation work in progress

The chat cancellation signal now reaches ToolRegistry and its handler context.
Code/Git/test and shell runners poll the context while waiting on the process.
Cancellation and timeout kill the process group and drain its pipes before returning.
Linux bubblewrap's PID namespace also destroys descendants that start a new session.
The tool check timeout is 305 seconds, accommodating its documented maximum
300-second process deadline and teardown.

Registry timeout uses an invocation-local cancellation event. It does not poison
the caller's event. A timed-out mutation is not automatically retried.
The registry waits for an in-process handler to finish before returning a terminal
timeout or cancellation result. Chat ownership and shutdown leases remain held
while handlers drain. A shutdown cannot claim a new completion notification.

Cancellation is not rollback. A file write or external request already performed
before the terminal result may have taken effect. Uncooperative Python handlers
can delay cancellation and shutdown until they exit. They are not forcibly killed
by a Python thread timeout.

Verified on 2026-11-14

- Local lifecycle/registry/coding tests: 26 tests, 24 passed and 2 Linux-only skips.
- Ubuntu isolated synthetic tests: 6 passed. Cancellation and timeout prevented a
  detached descendant's delayed file write. Host isolation remained intact.
- No model calls or production account changes were needed for these fixtures.

Durable workers update, 2026-11-15

Task and automation controls now persist `control_requested` independently of
the run state. A running worker retains its owner and lease until the current
handler drains; the API exposes the request and the UI shows "Останавливается…".
Cancellation can escalate a pending pause, but a later pause cannot downgrade
cancellation. An independent database connection can request the stop without
sharing Python objects with the worker.

`RunCancellation` checks persisted controls and the active deadline while an
instrument runs, and renews ownership during teardown. Both the deterministic
executor and LLM automation/long-task runner pass it into ToolRegistry. Generated
pure-data extensions use the same cancellable subprocess runner, with their
existing AST restrictions and resource limits preserved. Process stdin is sent
once across polling retries and preserves UTF-8.

Worker finalization checks the stop request under the same SQLite write lock as
the state transition. A late completion cannot overwrite it. Pending stops are
excluded from expired-lease replay. Paused tasks with started/cancelled/unknown
checkpoints require result review before resuming; automatic retry would risk
duplicating a side effect.

New fixtures cover a second-connection stop, cooperative cancellation,
uncooperative handler drain, pause-to-cancel escalation, tenant/worker isolation,
heartbeat during drain, expired pending stops, durable deadline before a delayed
write, subprocess cancellation, and Unicode/large stdin. These fixtures use
temporary databases and scripted providers, not production accounts or model APIs.

Validation: Ubuntu regression set covered 182 tests (178 passed, 2 Windows-only
skips, 2 HTTP-fixture failures caused by an obsolete dependency import). After
correcting that import and adding the remaining-time deadline fixture, the
affected 10-test subset passed. Combined unique coverage is 183 tests: 181 passed,
2 Windows-only skips. TypeScript checking and the frontend production build passed.
See `reports/qa/ave75-cancellation-2026-11-15.md` for evidence and limitations.

Remaining boundaries before closing AVE-75

- Arbitrary in-process Python handlers cannot be forcibly terminated safely.
  They retain ownership and remain stopping until they return. A hard upper bound
  requires moving such handlers into isolated worker processes.
- If a worker dies with a pending stop, the run deliberately stays unresolved
  rather than replaying an action with unknown effects. Operator reconciliation
  and its user flow still need implementation and acceptance tests.
- Full service shutdown/restart and pause/resume races need a dedicated acceptance
  sweep, beyond the existing chat shutdown and isolated worker fixtures.

Cancellation does not roll back writes or external requests already performed.
AVE-75 remains In Progress; this update is an incremental cancellation milestone.
