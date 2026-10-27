BEGIN;

CREATE TABLE automations (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name text NOT NULL,
    prompt text NOT NULL,
    schedule_type text NOT NULL CHECK (schedule_type IN ('once', 'weekly')),
    schedule_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    timezone text NOT NULL,
    human_rule text NOT NULL,
    status text NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused', 'completed', 'deleted')),
    next_run_at timestamptz,
    last_scheduled_for timestamptz,
    last_task_id uuid REFERENCES tasks(id) ON DELETE SET NULL,
    last_task_run_id uuid REFERENCES task_runs(id) ON DELETE SET NULL,
    budget_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    max_parallel_runs integer NOT NULL DEFAULT 1 CHECK (max_parallel_runs BETWEEN 1 AND 3),
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE automation_runs (
    id uuid PRIMARY KEY,
    automation_id uuid NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scheduled_for timestamptz NOT NULL,
    task_id uuid NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    task_run_id uuid NOT NULL UNIQUE REFERENCES task_runs(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (automation_id, scheduled_for)
);

CREATE INDEX idx_automations_due ON automations(status, next_run_at);
CREATE INDEX idx_automations_user_status ON automations(user_id, status, next_run_at);
CREATE INDEX idx_automation_runs_automation ON automation_runs(automation_id, scheduled_for DESC);

ALTER TABLE automations ENABLE ROW LEVEL SECURITY;
ALTER TABLE automation_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE automations FORCE ROW LEVEL SECURITY;
ALTER TABLE automation_runs FORCE ROW LEVEL SECURITY;

CREATE POLICY automations_user_isolation ON automations
    USING (user_id = current_setting('app.user_id', true)::uuid)
    WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
CREATE POLICY automation_runs_user_isolation ON automation_runs
    USING (user_id = current_setting('app.user_id', true)::uuid)
    WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);

COMMIT;
