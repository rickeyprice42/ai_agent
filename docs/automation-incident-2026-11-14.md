# Automation and search incident — 2026-11-14

Diagnosis used read-only queries against recent production automation occurrences,
their durable task state and tool messages. Private extracts remain outside Git.
Production before this repair was 0dda3ed3a72c291184bab9fd188866c7f8f4c800.

## Confirmed causes and repairs

1. The create form supplied the same "Еженедельный черновик" name to every task.
   Empty names now produce a descriptive local title. Existing placeholder names
   are repaired on listing, with user/name/prompt guards. Custom names, prompts,
   schedules and budgets are preserved. The form starts empty and clears after
   success. Its displayed budget now matches the 180k request budget.
2. Deliverable checks treated a tutorial mentioning "Word 2007" and input Word
   documents as a request for DOCX output. They now require an explicit delivery
   action and file/format intent. Actual DOCX delivery still requires a valid
   OOXML package.
3. A real site run attempted 59,452- and 26,037-character writes against the 20k
   note limit, then used unprefixed read paths. Automation-created text files
   now have a finite 100k ceiling advertised in the input schema. Relative reads
   resolve occurrence-local outputs first. Paged reading and exact editing tools
   are available to the automation agent. Normal user file limits are unchanged.
4. Large generated code was resent in each model round. Completed file-writing
   exchanges are now replayed as short untrusted observations, while full content
   remains in history and on disk. Source observations retain larger excerpts.
5. Automation reasoning requests were clipped to 30 seconds. The bounded request
   allowance is now up to 120 seconds, constrained by the remaining wall budget.
6. Research could prepare material and continue unnecessary file work until the
   action cap, preventing a final answer. Each request now sees its remaining
   action allowance. At zero, a final synthesis request has no tools. Hard limits
   still prevent further effects. No budget was silently increased.
7. Recurring lessons had no preceding occurrence context and sometimes said
   scheduling was unavailable. The run now states that scheduling is server-owned
   and supplies bounded excerpts from the same automation's last three successful
   occurrences, scoped by user and occurrence ordering.
8. Free search providers returned CAPTCHA or irrelevant results. A hosted OpenAI
   web-search fallback uses a required, completed search call and cited sources.
   Existing relevance/domain/catalog checks remain in force. Search timeout covers
   provider fallback and is no longer silently clipped to 20 seconds. A hosted
   timeout is not blindly replayed. Credentials never go to a custom endpoint.
9. Browser upstream timeout after client disconnect raised a noisy BrokenPipe
   traceback. It now produces a quiet gateway failure. Policy refusals stay 403.

Hosted search follows [the official Responses web-search contract](https://developers.openai.com/api/docs/guides/tools-web-search).
Its returned text remains discovery evidence. Final facts must be checked by
reading the original source. Hosted search introduces provider tool charges.

## Acceptance evidence

- Reproductions include the complete original Office lesson wording, a synthetic
  site write exceeding 59k characters, relative-path reading, placeholder repair,
  custom-name preservation, source fallback and final synthesis at the action cap.
- Mobile browser acceptance against the production frontend build passed at
  390×844. An empty title submits successfully and the form clears after success.
- Real gpt-6-luna runs used a fresh temporary database on Ubuntu. No production
  users, messages or schedules were created or rerun by these tests.
- The first live round reproduced failure in both lesson and research scenarios.
  After refinement, the full Office lesson completed with five successful tools
  and zero tool failures. Research completed in 15 actions with three recovered
  source failures. Estimated turn budgets were 29,945 and 94,755 tokens.
- Independent hosted-search smoke passed with real cited Qwen primary-source URLs.
- A broad Windows run passed 180 tests with five platform skips. Follow-up focused
  checks passed after Linux fixture corrections. The final broad Ubuntu run
  passed all 182 tests, including real isolated execution. Windows platform skips
  cover those Linux-only execution paths, not ignored production failures.

## Practical limits

Successful completion does not prove every generated sentence correct. Review of
the live research answer found an overbroad "last year" introduction despite an
older paper. Recency instructions were strengthened, but independent temporal and
claim-level acceptance still needs work. Source outages and access restrictions
remain possible and must be recovered or reported without fabricated evidence.

These changes do not implement an external Sites publishing integration inside
Avelin. Creating local HTML is distinct from publishing a hosted site. AVE-75's
remaining cancellation cases and AVE-82's actual usage accounting remain open.
