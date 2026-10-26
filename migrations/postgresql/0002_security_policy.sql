BEGIN;

CREATE TABLE security_settings (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    emergency_stop boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE approval_requests (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id uuid,
    step_id uuid,
    tool_name text NOT NULL,
    arguments_digest text NOT NULL,
    arguments_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    scope_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    risk text NOT NULL CHECK (risk IN ('read', 'write', 'external', 'high')),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'denied', 'consumed', 'expired', 'revoked')),
    expires_at timestamptz NOT NULL,
    approved_at timestamptz,
    decided_at timestamptz,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE permission_grants (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission text NOT NULL,
    scope_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked', 'expired')),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_approval_requests_user_status
    ON approval_requests(user_id, status, created_at DESC);
CREATE INDEX idx_approval_requests_exact_action
    ON approval_requests(user_id, tool_name, arguments_digest, status);
CREATE INDEX idx_permission_grants_user_status
    ON permission_grants(user_id, status, expires_at);

ALTER TABLE security_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE permission_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE security_settings FORCE ROW LEVEL SECURITY;
ALTER TABLE approval_requests FORCE ROW LEVEL SECURITY;
ALTER TABLE permission_grants FORCE ROW LEVEL SECURITY;

CREATE POLICY security_settings_user_isolation ON security_settings
    USING (user_id = current_setting('app.user_id', true)::uuid)
    WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
CREATE POLICY approval_requests_user_isolation ON approval_requests
    USING (user_id = current_setting('app.user_id', true)::uuid)
    WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
CREATE POLICY permission_grants_user_isolation ON permission_grants
    USING (user_id = current_setting('app.user_id', true)::uuid)
    WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);

COMMIT;
