# Shopping search context incident — 2026-11-14

## Root cause and choice

The reported stop was a local context-overflow guard, not exhaustion of the
OpenAI account balance or a shared user allowance. Raising a monetary allowance
would not fix it. This repair takes the efficiency path and preserves bounded
tool/turn limits. It does not introduce a shared spending pool or automatic
quota-reset scheduling.

A read-only diagnosis and disposable database replay reproduced the failure.
The previous product page was promoted from chat_tool history into the system
prompt's relevant long-term memory section. That section alone occupied 16,867
estimated tokens. The system prompt was 60,381 characters. Later tool compaction
could not shrink material embedded in that immutable system prompt.

The same turn had already obtained a structured product offer, but its error
handler stored only file artifacts. A browser visit to the image URL had created
a screenshot. That screenshot survived while the verified product object was lost.
This explains the unrequested shoe image and misleading file-oriented fallback.

## Repairs

- Keep raw chat_tool pages in history/tool observations, outside long-term system
  memory. Bound retrieved memory excerpts and reserve headroom for future tool
  observations. Preserve original user constraints and source data on disk.
- Pack observations proactively in ordinary chats as well as automations.
  Browser snapshots use the source-aware packer. Wide JSON has a final bounded
  fallback rather than relying only on depth and per-item limits.
- Preserve verified product objects in partial responses, clarification and
  fallback paths. Browser JSON-LD can also supply page-bound merchant offers.
- Browser reads default to no screenshot. Internal browser screenshots are not
  delivered as user files unless the user requested a capture.
- Shopping guidance prefers product pages over image URLs, explicitly separates
  price from requested-size availability and delivery, and uses the user's market.
  Obvious foreign storefronts are rejected for Russian shopping requests.
- Product cards remind users that size and delivery need separate confirmation.

## Evidence and limits

- Exact replay on a disposable copy: previously context_overflow, now REPLAY_OK.
  System prompt shrank to 13,986 characters with the same 32,768-token configured
  window. The replay fit without increasing model context or usage allowances.
- Windows and Ubuntu relevant regression suites passed 138 tests each. Another
  42 local tests covered personal/autonomous memory, compaction and shopping.
- Mobile production-build rendering passed at 390px with two product cards,
  merchant links and size/delivery caveats.
- Real gpt-6.1-sol continuation of a disposable copy completed. It delivered two
  structured product cards and zero file artifacts. Requested 42 RU availability
  was explicitly not claimed as confirmed. The production chat was not changed.
- Tests cover old large catalog pages, broad snapshots, screenshot defaults,
  explicit screenshot delivery, browser offers, partial-card persistence and
  Russian-market filtering. Synthetic regression inputs contain no private chats.

This does not bypass Lamoda or another merchant's access restrictions. A blocked
page remains unverified. A general product offer does not establish that a
particular size is in stock or that delivery to a particular city is available.
Shared spend quotas and provider-usage accounting remain separate work.
