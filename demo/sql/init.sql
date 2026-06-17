-- demo sql/init.sql — catalog DB schema
CREATE TYPE artifact_kind  AS ENUM ('log', 'dump');
CREATE TYPE artifact_data_type AS ENUM ('cdc', 'cds');
CREATE TYPE artifact_state AS ENUM ('pending', 'uploaded', 'failed', 'erased');

CREATE TABLE artifacts (
    id              BIGSERIAL PRIMARY KEY,
    kind            artifact_kind      NOT NULL,
    data_type       artifact_data_type NOT NULL,
    platform        TEXT               NOT NULL,
    app_release     TEXT               NOT NULL,
    uid             TEXT               NOT NULL,
    occurred_at     TIMESTAMPTZ        NOT NULL,
    bucket          TEXT               NOT NULL,
    object_key      TEXT               NOT NULL,
    size            BIGINT             NOT NULL,
    sha256          CHAR(64)           NOT NULL,
    crash_signature TEXT,
    meta            JSONB              NOT NULL DEFAULT '{}'::jsonb,
    state           artifact_state     NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ        NOT NULL DEFAULT now(),
    uploaded_at     TIMESTAMPTZ,
    deleted_at      TIMESTAMPTZ,
    UNIQUE (bucket, object_key)
);

CREATE INDEX artifacts_kind_state_occ_idx ON artifacts (kind, state, occurred_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX artifacts_platform_release_idx ON artifacts (platform, app_release, occurred_at DESC);
CREATE INDEX artifacts_uid_idx ON artifacts (uid, occurred_at DESC);
CREATE INDEX artifacts_crash_signature_idx ON artifacts (crash_signature) WHERE crash_signature IS NOT NULL;
CREATE INDEX artifacts_meta_gin ON artifacts USING GIN (meta);

CREATE VIEW dump_groups AS
SELECT
    crash_signature,
    platform,
    data_type,
    count(*) AS occurrences,
    max(occurred_at) AS last_seen,
    min(occurred_at) AS first_seen,
    count(DISTINCT uid) AS uids,
    count(DISTINCT app_release) AS releases,
    array_agg(DISTINCT app_release ORDER BY app_release DESC) AS release_list
FROM artifacts
WHERE kind = 'dump' AND state = 'uploaded' AND deleted_at IS NULL
GROUP BY crash_signature, platform, data_type;