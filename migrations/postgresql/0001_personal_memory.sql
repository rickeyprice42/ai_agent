BEGIN;

CREATE TYPE memory_kind AS ENUM ('profile', 'preference', 'episode', 'fact');
CREATE TYPE memory_scope AS ENUM ('global', 'project', 'chat');
CREATE TYPE memory_status AS ENUM ('active', 'superseded', 'revoked', 'expired');
CREATE TYPE memory_consent_status AS ENUM ('granted', 'revoked');
CREATE TYPE memory_store AS ENUM ('personal', 'health');

CREATE TABLE memory_records (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind memory_kind NOT NULL,
    content text NOT NULL CHECK (length(btrim(content)) > 0),
    scope memory_scope NOT NULL DEFAULT 'global',
    project_id uuid,
    thread_id uuid,
    confidence double precision NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
    status memory_status NOT NULL DEFAULT 'active',
    consent_status memory_consent_status NOT NULL DEFAULT 'granted',
    consented_at timestamptz NOT NULL,
    revoked_at timestamptz,
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((scope <> 'project') OR project_id IS NOT NULL),
    CHECK ((scope <> 'chat') OR thread_id IS NOT NULL)
);

-- Health data is physically separate so normal memory queries and exports cannot
-- include it by accident. Encryption-at-rest belongs to the deployment layer.
CREATE TABLE health_memory_records (LIKE memory_records INCLUDING ALL);
ALTER TABLE health_memory_records
    ADD CONSTRAINT health_memory_records_user_fk
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

CREATE TABLE memory_provenance (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_id uuid NOT NULL,
    memory_store memory_store NOT NULL,
    source_type text NOT NULL,
    source_id text,
    source_uri text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_memory_records_user_status ON memory_records(user_id, status);
CREATE INDEX idx_memory_records_scope ON memory_records(user_id, scope, project_id, thread_id);
CREATE INDEX idx_memory_records_content_fts
    ON memory_records USING gin (to_tsvector('simple', content));
CREATE INDEX idx_health_memory_records_user_status ON health_memory_records(user_id, status);
CREATE INDEX idx_memory_provenance_memory ON memory_provenance(user_id, memory_store, memory_id);

ALTER TABLE memory_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE health_memory_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_provenance ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_records FORCE ROW LEVEL SECURITY;
ALTER TABLE health_memory_records FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_provenance FORCE ROW LEVEL SECURITY;

-- The application must SET LOCAL app.user_id for every transaction.
CREATE POLICY memory_records_user_isolation ON memory_records
    USING (user_id = current_setting('app.user_id', true)::uuid)
    WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
CREATE POLICY health_memory_records_user_isolation ON health_memory_records
    USING (user_id = current_setting('app.user_id', true)::uuid)
    WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);
CREATE POLICY memory_provenance_user_isolation ON memory_provenance
    USING (user_id = current_setting('app.user_id', true)::uuid)
    WITH CHECK (user_id = current_setting('app.user_id', true)::uuid);

COMMIT;
