# Review checkpoint and AVE-75 milestone — 2026-11-15

## Linear review

Closed delivered implementations AVE-59, AVE-62, AVE-63, AVE-67, AVE-69,
AVE-70, AVE-71, AVE-92 and AVE-93 after reviewing descriptions, released code
and the owner's search acceptance. The In Review query returned no remaining
issues. Physical device acceptance and the outstanding research/long-horizon
matrix are explicitly retained in AVE-91. These closures do not establish that
every model response or device scenario is correct.

Before changes, 115 local regression tests passed. Production service was active
on `6de2e8be3c5fd5208cd33917559aed65b07c4180`, with public HTTPS returning 200.

## AVE-75 changes

- Persist a stop request while keeping the active run, worker and lease intact.
- Poll controls and active deadlines during tool execution, including controls
  written through an independent database connection.
- Renew leases during handler drain. Atomically prefer pending cancellation over
  a late completion. Pause can escalate to cancel, never the reverse.
- Prevent expired pending stops and uncertain checkpoints from blind replay.
- Apply cancellable subprocess execution to generated pure-data tools; retain
  AST restrictions and resource limits. Send subprocess stdin once across polls.
- Expose pending controls through task/automation API responses and display
  stopping in long-task and automation cards.
- Use the remaining total run time in the deterministic executor, and finalize
  the active step as failed when its deadline expires.

## Verification

All fixtures used temporary data and scripted providers; no model API or external
push was invoked and no production user, chat or file was edited by the tests.

Ubuntu tests ran as `avelin` in `/tmp/avelin-ave75-20261115`, using the production
Python environment and Chromium installation, separately from the live service.

The expanded 182-test run passed 178 tests, skipped 2 Windows process-tree tests,
and exposed 2 HTTP QA script failures: it imported `httpx` while the project now
depends on `httpx2`. After correcting the script and adding a deadline test, all
10 tests in the affected durable-worker/HTTP subset passed. Unique combined
coverage: **183 tests, 181 passed, 2 Windows-only skips**. No failures remain in
this selected regression set.

Server logs: `/tmp/ave75-acceptance.log`, `/tmp/ave75-final-corrections.log`.
The initial test environment also lacked fixture scripts/assets and the Chromium
path; those environment failures were corrected before the reported final runs.
Image approval tests now declare a fake key explicitly; their image/model calls
remain mocked instead of relying on a developer's local secrets.

Windows: 73 focused tests ran, 70 passed with 3 Linux-only skips. The corrected
image-approval/monitor subset passed 23 tests; actual HTTP disconnect/cancel passed
2 tests; the active-tool remaining-time test passed. The full durable coding test
requires Linux bubblewrap and was verified there. TypeScript checking and Vite
production build passed; Vite retains the existing bundle-size warning.

## Remaining work

AVE-75 stays In Progress. Arbitrary Python handlers still cannot be forcibly killed
inside a thread; they remain stopping and retain ownership until they drain.
A dead worker with a pending stop requires operator reconciliation, not replay.
That recovery flow and a dedicated full-service shutdown/restart race sweep are
still required. Cancellation does not undo an external effect already performed.

No physical iPhone/Android retest or real-model end-to-end acceptance was performed
in this milestone. Device checks remain AVE-91. This report distinguishes isolated
runtime verification from product acceptance.
