# Attached HTML game incident — 2026-11-16

Status: original root causes reproduced and corrected in the release candidate. A generated stage-2 HTML passed independent offline browser checks. The complete six-stage game remains incomplete and budget-limited; it must not be advertised as finished.

## Production evidence

- Deployed revision inspected: `487e1086662d773b09781514ec91247915591967`; service active.
- Failed plan: `c667c160-915b-409b-99ed-2ed27e6a33b1`; durable run: `48bc710c-4623-4528-ab0c-7edcf0f3eb48`.
- The originating chat successfully read the attached prototype. The long-task worker searched for the same attachment underneath its isolated output directory, where the attachment had never been staged.
- Original attachment still exists on the server: 21,585 bytes; SHA-256 `a636dfe48651794909c00fde7e36b4a44d40426de9b5271bd074e6dbb69ed3cb`.
- The worker saved BASELINE/STAGE2/STAGE3/STAGE4/CHECKS Markdown blocker reports. Successful note-writing/reading was treated as stage-completion evidence, despite explicit statements that stages were blocked. No requested HTML was produced by that run.
- Recorded run counters: 35 tool actions, 8 failures, 669,282 estimated tokens. These counters are the application's estimator, not audited provider usage or a monetary bill.

## Causes reproduced while investigating

1. Scoped long-task files excluded the originating message's explicit attachments.
2. Any verified tool result could count as completion of an entire stage. Saving a blocker report advanced dependent stages.
3. Generic tool-output compaction replaced large file-read JSON content with a 250-character excerpt while retaining the original range metadata. Models had to reread source code in tiny fragments.
4. A stage can reach the ordinary per-turn tool-round/call boundary while still inside its durable run budget. The old final phase exposed no completion-report mechanism. Experiments reproduced both an unfinished stage advancing and a genuinely prepared stage unable to report completion.
5. In the originating chat, `search_past_chats` returned a dict but its registered output contract defaulted to string; registry validation rejected an otherwise valid history lookup.

## Changes under verification

- Stage explicit user attachments into the isolated job using atomic copies; preserve originals and reject file-tool mutations of staged inputs. Do not grant access to the rest of the user workspace.
- Stop missing-input jobs before the first model call.
- Require a structured `report_long_task_step` and independent tool evidence. Blocked, missing, or unverified reports cannot advance dependent stages.
- Verify explicit output paths in stage descriptions and the final goal, including nonempty HTML deliverables.
- Preserve a bounded contiguous file-code prefix and return exact line/column continuations during compaction. Repeated compaction must reconstruct every character, including long/minified lines.
- Reserve only the stage-status tool in the final round, with no further filesystem/network work or extra model request. Ordinary chats retain their existing final-phase behavior.
- Correct the history-search output schema; test it through ToolRegistry, not only a direct Python function call.

## Verification evidence so far

- Final Windows suite: 84 tests run, 82 passed, 2 expected Linux-only skips.
- Final Linux suite: all 109 tests passed as the avelin user with the production Playwright path. Suites cover long tasks, independent task review, LLM automations, tool payloads, file contracts, reports, durable chat runs, and runtime audit.
- Real-model isolated attempts are recorded separately under the ignored `.local` directory and the isolated server candidate. Failed intermediate attempts are retained as evidence, not counted as success.
- Original production chats, accounts, and failed runs have not been rewritten or replayed.

## Earlier success claim

The November 11 report distinguished a small local real-model game test from the user's larger server game scenario. Server acceptance was still outstanding. That local test was insufficient evidence for a claim that this attached multi-stage server workflow worked.

## Real-model and browser acceptance

- Model: real `gpt-6.1-sol`, production-equivalent Linux runtime, isolated database and copied original attachment. No production run was silently replayed.
- Failed intermediate attempts reproduced missing completion handoffs and per-turn limits; none count as success.
- The model created a 26,843-byte HTML snapshot containing three selectable 3D courses. SHA-256: `ff1d2693901a360a4530bc21aa88985db97a432dc1e0ddf28f2fc2244e5d0bea`.
- Independent offline Edge check exercised start, keyboard movement, jump, pause/timer freeze, resume, restart, and menu on each of the three courses. No JavaScript page errors; WebGL scene visually inspected. All 43 written physics checks passed when actually executed independently.
- This snapshot has no campaign progression, moving obstacles or records. Full keyboard traversal of all courses was not performed. It is a stage-2 prototype, not the requested finished game.
- An audited isolated stage-2 run was resumed from its saved files after introducing safe same-stage continuation. It completed stage 2 without resetting cumulative usage; it then stopped during stage 3 at the existing 800,000 estimated-token ceiling (recorded usage 796,951; next call rejected before sending). Stages 4–6 did not run.
- This confirms file generation and honest intermediate results, and exposes a remaining efficiency/budget limitation of the six-stage plan. Increasing limits without improving task granularity and source handling is not counted as a fix.

## Safe continuation contract

- `continue` saves a bounded progress handoff and queues the SAME running stage, preserving tool/token/time counters and verified artifacts. It never marks the stage completed.
- A continuation requires new verified progress in that turn; unknown/cancelled/interrupted tool outcomes cannot be replayed. Cancellation wins the commit race and releases the worker after draining.
- Regression tests cover restart, unchanged pending stages, accumulated usage, no-progress loops, and cancellation racing the continuation commit.
- The final round exposes only the status handoff, including after the ordinary per-turn call ceiling. It cannot perform additional filesystem/network actions.
