BEGIN;

CREATE TABLE extension_proposals (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('skill', 'tool')),
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    manifest_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    permissions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    threat_model TEXT NOT NULL DEFAULT '',
    source_code TEXT NOT NULL DEFAULT '',
    skill_markdown TEXT NOT NULL DEFAULT '',
    tests_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    checks_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    diff_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    installed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, name, version)
);

CREATE INDEX idx_extensions_user_status
    ON extension_proposals(user_id, status, name);

ALTER TABLE extension_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE extension_proposals FORCE ROW LEVEL SECURITY;

CREATE POLICY extension_proposals_user_isolation ON extension_proposals
    USING (user_id::text = current_setting('app.user_id', true))
    WITH CHECK (user_id::text = current_setting('app.user_id', true));

COMMIT;
