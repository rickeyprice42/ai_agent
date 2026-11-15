# AVE-75 — orphan closure and durable shutdown

## Delivered behavior

A lease timeout no longer authorizes a second worker to take work from a live
process. Claims persist the host/boot/process creation identity and Linux service
cgroup/invocation. Orphan recovery requires expired ownership and proof that both
the old worker and its execution domain have stopped. A dead parent PID alone is
insufficient because a detached child could continue writing.

Authenticated owners can close a proven-stopped orphan through the task queue,
automation or long-task card. A note, acknowledgement and current snapshot token
are required. The transaction checks identity/containment again, preserves prior
results and unknown checkpoint evidence, revokes this run's pending approvals,
and saves an audit entry. It closes the run as cancelled without replay or rollback.
Other accounts and stale decisions are rejected. Missing legacy identity and
unverifiable containment remain blocked instead of inventing a successful stop.

Shutdown now signals durable workers and awaits the underlying work before
releasing it. Active tools stop into a persisted pause; uncertain checkpoints
cannot be blindly resumed. Old unresolved runs cannot starve fresh queued work.

## Evidence

- Ubuntu isolated regression: 191 tests, 189 passed, 2 Windows-only skips.
  Log: `/tmp/ave75-recovery-regression.log`.
- Final recovery subset: 10/10 passed on Windows and Ubuntu, including full
  FastAPI lifespan shutdown and approval revocation isolation. This adds two
  tests to the earlier regression set: unique coverage **193 tests, 191 passed,
  2 Windows-only skips**. Log: `/tmp/ave75-recovery-shutdown-final.log`.
- An actual temporary systemd unit ran a synthetic worker and a child with a new
  session that attempted a delayed write. Recovery was blocked while live. After
  stopping/restarting that same unit, recovery succeeded; the child never wrote
  its delayed file, unknown evidence remained, and a second recovery was rejected.
  Result: `/tmp/avelin-ave75-recovery-state-20261115/result.json`.
  Reproducers: `scripts/verify_task_recovery.py` and
  `scripts/verify_task_recovery_systemd.sh`. The temporary unit was removed.
- Headless Edge: four scenarios passed — desktop 1100px, mobile 390px, containment
  blocked, stale-decision conflict. Explicit consent/note gating and no horizontal
  overflow were checked; mobile screenshot was inspected. These used mocked API
  responses; backend authorization was tested separately over TestClient.
  Artifacts: `.local/ave75-recovery-ui/`.
- TypeScript checking and production frontend build passed.

Tests used temporary databases and scripted providers. Model API calls: zero.
The production service was not stopped by the temporary systemd acceptance test.
No production accounts, messages, files or automations were edited by the tests.
No physical iPhone/Android or model-quality acceptance is inferred from these tests.

## Remaining AVE-75 boundary

An arbitrary uncooperative Python handler is still an in-process thread. It keeps
ownership while draining and cannot be safely force-killed as a thread. A separate
isolated process host is required to impose a hard per-tool termination deadline
without restarting the whole service. AVE-75 therefore remains In Progress.
