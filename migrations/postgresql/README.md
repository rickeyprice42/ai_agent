# PostgreSQL migration contract

The numbered files are the production PostgreSQL schema contracts:

- `0001_personal_memory.sql` covers AVE-36 personal memory.
- `0002_security_policy.sql` defines the security and approval policy.
- `0003_durable_task_runs.sql` adds durable task runs and checkpoints.
- `0004_task_reports_notifications.sql` adds AVE-42 evidence reports and
  in-app notifications.
- `0005_automations.sql` adds AVE-54 schedules and deduplicated occurrences.

The current desktop launcher continues to use SQLite; equivalent tables are
created by `AvelinDatabase.initialize()` so local installations remain
zero-configuration.

Before executing application queries in PostgreSQL, the future adapter must run
`SET LOCAL app.user_id = '<authenticated-user-uuid>'` in the same transaction.
Row-level security is defense in depth; API and repository queries must still
filter by `user_id`.

Health records are stored separately and are excluded from ordinary list and
export operations. They require an explicit `include_sensitive=true` request.
