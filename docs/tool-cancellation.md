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

Still required before closing AVE-75

- Carry cancellation and deadline monitoring through durable task/automation
  workers, including controls received by a different server process.
- Distinguish a stop request from confirmed worker teardown in task state/API.
- Cover generated-tool execution and bounded termination of uncooperative
  handlers. Keep uncertain side effects explicit and prevent blind replay.
- Verify pause/resume, lease recovery and shutdown races with these changes.

This cancellation foundation is included in the automation reliability release
prepared on 2026-11-14. AVE-75 remains in progress for the remaining durable-task,
generated-tool and uncooperative-handler cases listed above.
