# PostgreSQL migration contract

`0001_personal_memory.sql` is the production schema contract for AVE-36. The
current desktop launcher continues to use SQLite; its equivalent tables are
created by `AvelinDatabase.initialize()` so local installations remain
zero-configuration.

Before executing application queries in PostgreSQL, the future adapter must run
`SET LOCAL app.user_id = '<authenticated-user-uuid>'` in the same transaction.
Row-level security is defense in depth; API and repository queries must still
filter by `user_id`.

Health records are stored separately and are excluded from ordinary list and
export operations. They require an explicit `include_sensitive=true` request.
