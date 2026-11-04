# Reusable tools and Skills

Avelin can propose a small reusable pure-data tool or an instruction-only Skill
from the chat. This extends the existing Tool Factory; it is not unrestricted
Python execution and does not reproduce ChatGPT's private implementation.

1. The model searches existing capabilities with `tool_search`. Existing file,
   image, browser and office tools should be reused.
2. `propose_extension` saves an immutable version with its schemas, source or
   instructions, threat model and test definitions. It performs static checks
   only. **Generated code is not executed at this stage.** A review ZIP appears
   under `outputs/extensions/<name>/<version>/` and contains `SKILL.md`,
   `manifest.json`, `tests.json`, `REVIEW.md`, and `tool.py` for a code tool.
   Contract cases declare `input` and `expected`; the compatible `arguments`
   spelling is normalized to `input`. Conflicting spellings are rejected.
   A rejected immutable draft is corrected as a suggested new patch version;
   preparing that draft does not require an extra confirmation.
3. `install_extension` includes the proposal ID, version and exact content SHA-256.
   The existing confirmation dialog pauses the chat. Approval authorizes contract
   tests and activation of that exact version; rejection runs no generated code.
4. Tests execute in a separate Python process with `-I -S`, an empty temporary
   working directory and empty environment. The AST allowlist excludes imports,
   attribute access, calls, loops and I/O. Supported JSON expressions, arithmetic,
   concatenation and simple formatting have allocation limits. Input/output is
   limited to 256 KB, sequences to 50,000 items/characters, and execution to five
   seconds. Installation has an eight-second aggregate contract-test budget;
   cancellation/timeout prevents late activation and late result writes.
   This is a restricted expression worker, **not a general OS sandbox**.
5. Passing versions activate immediately. The approved instructions are also
   materialized as `skills/<name>/<version>/SKILL.md`. The approved database
   version remains authoritative; editing that file does not silently activate
   a new version.
6. Calling generated code goes through per-action confirmation, including cache
   hits. New JSON results are downloadable artifacts under `outputs/`.

Only tools explicitly marked `idempotent` use the local result cache. Its key
includes owner, exact approved content/version/permissions and canonical input.
The cache survives an application restart, expires after seven days, and is
bounded to 128 entries / 16 MB per user workspace. Disabling, replacing or
changing an installed version prevents stale execution. Cache entries do not
grant permissions. The JSON result is checked against the approved output schema.
Content/result checksums detect cache corruption. If a downloaded result file has
been changed, a repeated run preserves that file and links a new verified copy.

Skills contain reusable instructions. They do not create capabilities by
themselves, install dependencies, gain network access or override user/policy
instructions. More capable generated programs require a separate hardened
execution environment; Avelin should explain that limitation instead of inventing
an ability or running a broader script automatically.
