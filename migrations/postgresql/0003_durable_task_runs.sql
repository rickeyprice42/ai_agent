BEGIN;

CREATE TABLE task_runs (
    id uuid PRIMARY KEY,
    task_id uuid NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    state text NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'running', 'waiting_approval', 'paused', 'failed', 'completed', 'cancelled')),
    worker_id text,
    lease_expires_at timestamptz,
    heartbeat_at timestamptz,
    plan_version integer NOT NULL DEFAULT 1,
    deduplication_key text NOT NULL UNIQUE,
    budget_json jsonb NOT NULL DEFAULT '{"max_steps":50,"max_failures":3,"max_seconds":3600,"max_tokens":100000,"max_cost_micros":0}'::jsonb,
    usage_json jsonb NOT NULL DEFAULT '{"steps":0,"failures":0,"tokens":0,"cost_micros":0}'::jsonb,
    failure_reason text,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE task_checkpoints (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    task_id uuid NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_id uuid,
    sequence integer NOT NULL,
    plan_version integer NOT NULL,
    status text NOT NULL,
    risk text NOT NULL CHECK (risk IN ('read', 'write', 'external', 'high')),
    tool_name text,
    arguments_digest text,
    deduplication_key text NOT NULL,
    tool_call_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    observation text NOT NULL DEFAULT '',
    artifacts_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    next_action text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence),
    UNIQUE (run_id, deduplication_key)
);

CREATE INDEX idx_task_runs_queue ON task_runs(user_id, state, created_at);
CREATE INDEX idx_task_runs_lease ON task_runs(state, lease_expires_at);
CREATE INDEX idx_task_checkpoints_run ON task_checkpoints(run_id, sequence);

ALTER TABLE task_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE task_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE task_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE task_checkpoints FORCE ROW LEVEL SECURITY;

CREATE POLICY task_runs_user_isolation ON task_runs
    USING (user_id = current_setting('app.user_id', true)::uuid)
    WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
CREATE POLICY task_checkpoints_user_isolation ON task_checkpoints
    USING (run_id IN (SELECT id FROM task_runs WHERE user_id = current_setting('app.user_id', true)::uuid))
    WITH CHECK (run_id IN (SELECT id FROM task_runs WHERE user_id = current_setting('app.user_id', true)::uuid));

COMMIT;
