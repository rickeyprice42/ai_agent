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

Recovery and service shutdown update, 2026-11-15

Durable claims now record the host, boot, process creation identity, and Linux
service execution domain. A stale heartbeat cannot transfer a live worker's run.
Automatic stale recovery also requires proof that the old execution domain has
stopped and no uncertain checkpoint exists. Unknown older claims stay unresolved.

`TaskRecovery` lets an authenticated owner close an orphan as cancelled after
the lease has expired and execution is proven stopped. A stopped parent PID alone
is insufficient: detached children could still run. On Ubuntu proof requires an
OS reboot, or a new invocation of the same systemd service with no remaining old
processes in its cgroup. Unsupported containment and missing identities fail
closed. This is conservative while unrelated new subprocesses occupy that group.

The owner reviews results, adds a note and explicitly acknowledges unknown
effects. A snapshot token and a second containment check under a SQLite write lock
reject stale/conflicting decisions. Recovery preserves observations/artifacts,
marks uncertain checkpoints interrupted without verified success, revokes only
the pending approvals linked to this run, and records an audit entry. It never
resumes or repeats the unknown action. UI controls are available in long tasks,
automations and the task queue when a run needs recovery.

Service shutdown sets the durable stop signal before draining the real background
work. It does not cancel an asyncio wrapper and assume its Python thread exited.
Active tools receive a persisted pause, retain the lease while draining, and do
not launch another queued action. Resuming uncertain checkpoints stays blocked.

Ubuntu verification: 191-test regression run passed 189 tests with 2 Windows-only
skips. The final 10-test recovery subset, including two additional full-lifespan
shutdown/approval tests, passed. Unique combined coverage: 193 tests, 191 passed,
2 skips. A separate temporary systemd service proved live-worker recovery stays
blocked, service restart kills a detached delayed writer, and orphan closure
preserves unknown evidence without replay. Four headless Edge UI scenarios passed
at desktop/mobile widths; no physical phone or real-model acceptance is claimed.

Remaining boundaries before closing AVE-75

- The isolated Python host now supports trusted importable handlers and is enabled
  for 15 local file/Office/image tools on Ubuntu. It terminates ignoring handlers
  and detached descendants without restarting the service. Read-only mounts and
  per-job long-task mounts preserve permissions and data isolation. See
  `docs/process-tool-host.md` for its contract and platform boundaries.
- Claims from older versions without process identity, different/unverifiable
  hosts, and unsupported containment cannot use automatic recovery. They require
  a separately verified operator procedure. Recovery closes a run; it does not
  establish whether an external action succeeded or implement partial replay.
- Remaining network/stateful/connector callbacks still require reconstructable
  profiles and validated egress before moving out of process. They retain the
  cooperative/drain ownership path; uncooperative legacy threads cannot be safely
  force-killed individually. No universal hard-stop guarantee is claimed for them.

File writers now stage complete outputs before atomic publication. A failed or
killed build cannot expose a partially replaced target. Unknown mutation outcomes
stop the Agent batch/model loop and persist interrupted checkpoints. Idempotency
alone no longer enables automatic mutation retries after timeout. The approved-
action path also records unknown outcomes as interrupted and avoids false
completion/synthesis calls. Cancellation stops remaining calls in the same batch.

Cancellation does not roll back writes or external requests already performed.
AVE-75 remains In Progress for migration of the remaining legacy/network profiles.
The isolated local-tool host, supported orphan closure and tested durable shutdown
scenarios are implemented; their evidence is kept separately from model acceptance.
