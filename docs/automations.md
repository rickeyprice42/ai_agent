# Automation execution

Literal local commands, such as creating a document with explicitly supplied text, keep the fast executor path. Free-form requests and research use the regular Agent multi-tool loop in a dedicated dialogue for that occurrence. Research is routed to the Agent before any literal DOCX parsing, so a compound request cannot silently become a document containing the research instruction itself.

The automation dialogue uses the account's selected model, its consented memory retrieval and the existing tool policy. Its safe tool set includes search, source reading, browser reading, HTTP reads and file/document creation. Unattended text research uses HTTP/DOM first and does not advertise cloud image analysis; public article screenshots do not require sending images to a vision provider. Permissions for external actions remain interactive. Scheduler prompts are not extracted into personal memory. New outputs stay under `automations/<automation-id>/<run-id>/...`.

The run stores the final answer, artifacts and its dialogue ID. Open that dialogue to inspect sources or approve a waiting action. Approval continues the same action and original request, including after the Agent object is recreated. Denial cancels that occurrence. Agent partial outcomes and provider failures retain useful files without reporting completion. Cancellation, pause and worker ownership are checked again in the same transaction as final state writes.

For research requests, a completed model turn alone is insufficient: a successful source read and links in the final response are required. Requested document formats also require a created artifact. Word requests require a DOCX ZIP containing Word document data; a screenshot alone cannot fulfill them. These checks verify execution evidence, not the factual quality of every claim; source dates, methodology and interpretation remain part of research evaluation.

Known limitation: execution completion is not a semantic fulfillment certificate. A model can deliver an explicitly limited report with fewer independent studies than requested and still complete the execution protocol. The November 4 live runs produced one-study partial reports, not three-study reviews; the subsequent November 5 retry failed its error budget. Structured coverage/recency verification remains open in AVE-64. Internal search-convergence guidance is now temporary system state: reading a source reopens search and the guidance is never sent as a user instruction.

## Persistence and limits

New automations use a bounded default of 20 tool actions, 5 failed actions,
180,000 estimated input/output tokens and 1,800 active seconds. The earlier
50,000/3 stock preset predated multi-tool research and was lower than the observed
85,980 tokens/four failed reads in a successful real research run. Explicit saved
custom budgets are preserved. `scripts/recommend_automation_budgets.py` creates a
read-only, narrowly filtered plan; an explicit apply makes a backup and uses CAS.
The production plan during this repair had no eligible active research entries.

- Tool starts and results are checkpointed. Final artifacts are checked against the occurrence namespace and file existence before reporting.
- Completed plan steps are retained. A later step is queued separately; the task is completed after its final step.
- A lost worker lease stops further actions and cannot overwrite the replacement worker's state.
- An interrupted process with unknown execution state fails visibly instead of replaying possible side effects. A manual retry creates a fresh occurrence. Inspect the old dialogue and files before repeating external actions.
- Step and failure limits are checked before further work. Active elapsed time is accumulated across segments; time spent waiting for approval is excluded.
- Token accounting is a conservative estimate of input/output text and tool schemas. It is not the provider's billing usage. A positive monetary limit is rejected for Agent-backed runs while verified cost accounting is unavailable.
- Provider calls have a bounded timeout. Cancellation prevents the next action; a currently blocking tool/provider call must return or reach its timeout first.

Scheduling ticks and the queue consumer run independently. One long manual research run does not prevent other due occurrences from being queued. The consumer executes one occurrence at a time; this change does not introduce a parallel worker fleet.

## Chat title repair

New greeting-only messages do not become permanent topic titles. The first substantive request supplies the local topic fallback and background model job. Pure greeting model outputs are rejected.

`scripts/rebuild_chat_titles.py` defaults to a read-only plan for placeholders and exact matches to the historical automatic-title formula. An explicit `--apply` makes a SQLite backup and uses title/source compare-and-swap guards. Known manual-title markers and nonmatching custom names are preserved. Historical manual names identical to the old automatic formula cannot be distinguished perfectly; review the plan before applying it.
