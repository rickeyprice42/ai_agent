BEGIN;

CREATE TABLE task_reports (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    report_key text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('run', 'daily')),
    report_date date NOT NULL,
    run_id uuid REFERENCES task_runs(id) ON DELETE CASCADE,
    task_id uuid REFERENCES tasks(id) ON DELETE CASCADE,
    payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, report_key)
);

CREATE TABLE in_app_notifications (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    deduplication_key text NOT NULL,
    kind text NOT NULL,
    title text NOT NULL,
    body text NOT NULL DEFAULT '',
    entity_type text,
    entity_id text,
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, deduplication_key)
);

CREATE INDEX idx_task_reports_user_date ON task_reports(user_id, report_date, updated_at DESC);
CREATE INDEX idx_notifications_user_read ON in_app_notifications(user_id, read_at, created_at DESC);

ALTER TABLE task_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE in_app_notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE task_reports FORCE ROW LEVEL SECURITY;
ALTER TABLE in_app_notifications FORCE ROW LEVEL SECURITY;

CREATE POLICY task_reports_user_isolation ON task_reports
    USING (user_id = current_setting('app.user_id', true)::uuid)
    WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
CREATE POLICY in_app_notifications_user_isolation ON in_app_notifications
    USING (user_id = current_setting('app.user_id', true)::uuid)
    WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);

COMMIT;
