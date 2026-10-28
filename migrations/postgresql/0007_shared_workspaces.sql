BEGIN;

ALTER TABLE projects ADD COLUMN shared BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE projects ADD COLUMN agent_mode TEXT NOT NULL DEFAULT 'mention_only'
    CHECK (agent_mode IN ('mention_only', 'explicit', 'off'));
ALTER TABLE projects ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE messages ADD COLUMN author_user_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE TABLE project_members (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed')),
    invited_by UUID REFERENCES users(id) ON DELETE SET NULL,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    removed_at TIMESTAMPTZ,
    PRIMARY KEY (project_id, user_id)
);

INSERT INTO project_members (project_id, user_id, role, status, invited_by)
SELECT id, user_id, 'owner', 'active', user_id FROM projects
ON CONFLICT DO NOTHING;

CREATE TABLE project_invitations (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('editor', 'viewer')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'revoked', 'expired')),
    token_hash TEXT NOT NULL UNIQUE,
    invited_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_by UUID REFERENCES users(id) ON DELETE SET NULL,
    accepted_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE shared_project_items (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('task', 'decision', 'artifact', 'memory')),
    title TEXT NOT NULL,
    content_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_type TEXT,
    source_id TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE project_audit_events (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    actor_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chat_read_states (
    thread_id UUID NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_read_message_id UUID,
    last_read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (thread_id, user_id)
);

CREATE INDEX idx_project_members_user ON project_members(user_id, status, project_id);
CREATE INDEX idx_project_invites_email ON project_invitations(email, status, expires_at);
CREATE INDEX idx_shared_items_project ON shared_project_items(project_id, status, updated_at DESC);
CREATE INDEX idx_project_audit_project ON project_audit_events(project_id, created_at DESC);

ALTER TABLE project_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE shared_project_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_read_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_members FORCE ROW LEVEL SECURITY;
ALTER TABLE project_invitations FORCE ROW LEVEL SECURITY;
ALTER TABLE shared_project_items FORCE ROW LEVEL SECURITY;
ALTER TABLE project_audit_events FORCE ROW LEVEL SECURITY;
ALTER TABLE chat_read_states FORCE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION app_project_role(target_project_id UUID)
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
SET row_security = off
AS $$
    SELECT CASE
        WHEN p.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid THEN 'owner'
        ELSE (
            SELECT pm.role
            FROM project_members pm
            WHERE pm.project_id = p.id
              AND pm.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
              AND pm.status = 'active'
        )
    END
    FROM projects p
    WHERE p.id = target_project_id
$$;

CREATE POLICY project_members_read ON project_members
    FOR SELECT USING (app_project_role(project_id) IS NOT NULL);
CREATE POLICY project_members_owner_write ON project_members
    FOR UPDATE USING (app_project_role(project_id) = 'owner')
    WITH CHECK (app_project_role(project_id) = 'owner');
CREATE POLICY project_members_owner_delete ON project_members
    FOR DELETE USING (app_project_role(project_id) = 'owner');
CREATE POLICY project_members_invitee_insert ON project_members
    FOR INSERT WITH CHECK (
        user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        AND EXISTS (
            SELECT 1
            FROM project_invitations pi
            JOIN users u ON lower(u.email) = lower(pi.email)
            WHERE pi.project_id = project_members.project_id
              AND u.id = project_members.user_id
              AND pi.status = 'pending'
              AND pi.expires_at > now()
        )
    );

CREATE POLICY project_invitations_owner_all ON project_invitations
    FOR ALL USING (app_project_role(project_id) = 'owner')
    WITH CHECK (app_project_role(project_id) = 'owner');
CREATE POLICY project_invitations_invitee_read ON project_invitations
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM users u
            WHERE u.id = NULLIF(current_setting('app.user_id', true), '')::uuid
              AND lower(u.email) = lower(project_invitations.email)
        )
    );
CREATE POLICY project_invitations_invitee_accept ON project_invitations
    FOR UPDATE USING (
        status = 'pending'
        AND expires_at > now()
        AND EXISTS (
            SELECT 1 FROM users u
            WHERE u.id = NULLIF(current_setting('app.user_id', true), '')::uuid
              AND lower(u.email) = lower(project_invitations.email)
        )
    )
    WITH CHECK (accepted_by = NULLIF(current_setting('app.user_id', true), '')::uuid);

CREATE POLICY shared_project_items_read ON shared_project_items
    FOR SELECT USING (app_project_role(project_id) IS NOT NULL);
CREATE POLICY shared_project_items_create ON shared_project_items
    FOR INSERT WITH CHECK (
        app_project_role(project_id) IN ('owner', 'editor')
        AND created_by = NULLIF(current_setting('app.user_id', true), '')::uuid
    );
CREATE POLICY shared_project_items_write ON shared_project_items
    FOR UPDATE USING (app_project_role(project_id) IN ('owner', 'editor'))
    WITH CHECK (app_project_role(project_id) IN ('owner', 'editor'));

CREATE POLICY project_audit_events_read ON project_audit_events
    FOR SELECT USING (app_project_role(project_id) IS NOT NULL);
CREATE POLICY project_audit_events_create ON project_audit_events
    FOR INSERT WITH CHECK (
        app_project_role(project_id) IS NOT NULL
        AND actor_user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    );

CREATE POLICY chat_read_states_user_isolation ON chat_read_states
    FOR ALL USING (
        user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        AND EXISTS (
            SELECT 1 FROM chat_threads ct
            WHERE ct.id = chat_read_states.thread_id
              AND (
                  ct.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                  OR app_project_role(ct.project_id) IS NOT NULL
              )
        )
    )
    WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), '')::uuid);

COMMIT;
