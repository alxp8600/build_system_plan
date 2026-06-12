-- sdk-build-plan/scripts-stub/sql/init.sql
-- catalog DB 初始化脚本 (Postgres 14+)
--
-- 一张主表 artifacts 覆盖 log/dump 两类。
-- 索引面向 decrypt-proxy 的 search 查询 (按 channel/release/device/sig/time)。

CREATE TYPE artifact_kind  AS ENUM ('log', 'dump');
CREATE TYPE artifact_state AS ENUM ('pending', 'uploaded', 'failed', 'erased');

CREATE TABLE artifacts (
    id              BIGSERIAL PRIMARY KEY,
    kind            artifact_kind  NOT NULL,
    product         TEXT           NOT NULL,
    channel         TEXT           NOT NULL,                 -- dev/staging/release
    app_release     TEXT           NOT NULL,                 -- product@version+sha
    platform        TEXT           NOT NULL,                 -- android/ios/windows/linux/macos
    os              TEXT           NOT NULL,
    device_id       TEXT           NOT NULL,
    occurred_at     TIMESTAMPTZ    NOT NULL,                 -- 客户端发生时间
    bucket          TEXT           NOT NULL,
    object_key      TEXT           NOT NULL,
    size            BIGINT         NOT NULL,
    sha256          CHAR(64)       NOT NULL,
    crash_signature TEXT,                                    -- 仅 dump
    meta            JSONB          NOT NULL DEFAULT '{}'::jsonb,
    state           artifact_state NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT now(),
    uploaded_at     TIMESTAMPTZ,
    deleted_at      TIMESTAMPTZ,
    UNIQUE (bucket, object_key)
);

-- 主搜索路径
CREATE INDEX artifacts_kind_state_occ_idx
    ON artifacts (kind, state, occurred_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX artifacts_product_channel_release_idx
    ON artifacts (product, channel, app_release, occurred_at DESC);

CREATE INDEX artifacts_device_idx
    ON artifacts (device_id, occurred_at DESC);

CREATE INDEX artifacts_crash_signature_idx
    ON artifacts (crash_signature)
    WHERE crash_signature IS NOT NULL;

CREATE INDEX artifacts_meta_gin ON artifacts USING GIN (meta);

-- 软删除 webhook: 由 MinIO bucket notification → decrypt-proxy → 这里 UPDATE
-- UPDATE artifacts SET deleted_at = now() WHERE bucket=$1 AND object_key=$2;

-- ----------------------------------------------------------------------
-- crash 聚合视图: 用于"按 crash_signature 折叠"列表
-- ----------------------------------------------------------------------
CREATE VIEW dump_groups AS
SELECT
    crash_signature,
    product,
    channel,
    count(*)            AS occurrences,
    max(occurred_at)    AS last_seen,
    min(occurred_at)    AS first_seen,
    count(DISTINCT device_id) AS devices,
    count(DISTINCT app_release) AS releases,
    array_agg(DISTINCT app_release ORDER BY app_release DESC) AS release_list
FROM artifacts
WHERE kind = 'dump' AND state = 'uploaded' AND deleted_at IS NULL
GROUP BY crash_signature, product, channel;

-- ----------------------------------------------------------------------
-- 角色 / 账号 (示例, 实际通过 vault 注入)
-- ----------------------------------------------------------------------
-- CREATE ROLE upload_token  LOGIN PASSWORD '...';
-- CREATE ROLE decrypt_proxy LOGIN PASSWORD '...';
-- GRANT SELECT, INSERT, UPDATE ON artifacts TO upload_token;
-- GRANT SELECT, UPDATE         ON artifacts TO decrypt_proxy;
-- GRANT USAGE, SELECT ON SEQUENCE artifacts_id_seq TO upload_token;