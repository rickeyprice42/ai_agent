# Isolated Python tool host (AVE-75)

Use `HostedHandler(ProcessHandlerSpec(...))` for a trusted, importable Python
handler with the signature `run(arguments, configuration)`. Both inputs and the
result must fit the JSON contract. Return a `ToolResult` for explicit failures;
an ordinary JSON value is a successful raw result. The server owns the descriptor:
it is never a model-supplied import path, generated manifest or pickle payload.

The handler is imported in a fresh Python interpreter inside Linux bubblewrap.
Only the Python runtime, read-only code roots and the authorized workspace are
mounted. Network, host environment, other account data and writable import roots
are absent. The workspace is read-only by default. Writable profiles require a
declared write/high permission when registered. Existing policy checks and input
validation still happen in the parent before execution.

The child receives no live Agent, database connection, lock, token cache or client
object. There is no reuse of forked Python handler state. Long-task profiles bind
only the task directory at its original logical prefix, preserving tool paths
while hiding sibling jobs. Runtime read/write limits are snapshotted per call.

The parent polls cancellation/deadline and kills/drains the entire PID namespace.
Handlers cannot evade teardown by ignoring an Event or creating a new session.
Returning successfully also ends detached descendants: asynchronous work must not
outlive the tool call. The parent keeps ownership/leases until teardown completes.

The worker limits address space to 1 GiB and CPU time to its deadline allowance.
The private JSON channel is capped at 2 MiB in each direction. Incidental stdout,
stderr and inherited subprocess output go to `/dev/null`; the private protocol FD
is not inherited by exec'd children. Exceptions and protocol failures do not expose
worker stderr/configuration. Production file validators may expose their known
input errors; arbitrary module ValueErrors are redacted by default.

On Ubuntu, these 15 tools now use the host:

- `read_file`, `read_file_lines`, `read_pdf_pages`, `file_metadata`;
- `list_workspace_files`, `search_workspace`, `write_file`, `edit_file_exact`;
- `create_docx`, `append_docx`, `create_presentation`;
- `inspect_image`, `transform_image`, `inspect_office_document`, `edit_office_document`.

Text, DOCX/PPTX, image and Office-copy outputs use same-directory staging and
atomic publication. Validation/build failure leaves the previous target intact.
`overwrite=False` uses atomic no-clobber publication. A hard-killed worker can
leave a hidden `.pending` staging file; it is not listed/delivered as a result.
This is not rollback of a file already published or a remote action already sent.

A writable handler timeout, process/protocol failure or unclassified exception
is an unknown outcome. `validation_errors_safe=True` is reserved for known file
validators that reject before publication; it must not hide a failure after a
side effect. Unknown writes are never automatically retried, even if a tool claims
idempotency. Agent stops the current batch/model loop; the checkpoint is interrupted
and unverified. Approved actions stay interrupted rather than being marked done
or passed to another synthesis round as already completed.

Explicit host calls fail closed on unsupported platforms. Existing Windows file
tools retain their cooperative execution path and use the atomic writers; there
is no Windows hard-kill guarantee from this Linux host.

The remaining AVE-75 migration covers network/stateful/connector handlers, which
cannot safely inherit clients or gain unfiltered network access. They retain the
drain/ownership semantics until explicit reconstructable profiles and validated
egress are implemented. The host is not yet a universal execution contract for
every legacy callback. Code/Git/shell and generated pure-data tools keep their
existing process runners. Direct low-level sandbox calls outside ToolRegistry do
not acquire this host's lifecycle guarantees.
