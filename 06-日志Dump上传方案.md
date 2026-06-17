# 06 日志 / Dump 上传方案（对应核心问题 3、4 上半）

> 一句话：**客户端直接"打包 → 命名" → 向 `upload-token-service` 换一次性 presigned URL → PUT 到 MinIO**。服务端不做处理，直接存储原始文件。

## 1. 总体流程

```
APP / SDK 进程
   │
   │ 1) 触发条件：
   │    - 用户主动反馈
   │    - crash 回写
   │    - SDK 内部异常上报阈值
   │
   ▼
  本地打包:
    logs/*.log → zip 压缩 → 文件名规范
    dump/*.dmp → 直接打包（zip）
    ─────────────────────────────────────────
    client 调用:
         POST https://token.intra/v1/presign
         Body  : { kind, extension, data_type, platform, version, uid }
    ←  服务端返回:
         { upload_url: "https://s3.intra/sdk-logs/xxxxx?X-Amz-Signature=...",
           key: "...", bucket: "sdk-logs" }
    ─────────────────────────────────────────
    PUT 文件流 → MinIO （直连，不过应用服务器）
```

**核心收益**：
- 应用服务器只签 URL，不过流量 → 上百 GB 日志也不会压垮。
- 客户端没有 S3 access key，无法越权访问别人的目录。

## 2. upload-token-service（自写 ~40 行 FastAPI）

接口：

| Method/Path | 说明 |
| --- | --- |
| `POST /v1/presign` | 接收客户端参数（kind, extension, data_type, platform, version, uid），校验参数合法性，调用 `boto3.generate_presigned_url('put_object', ExpiresIn=3600)` 返回上传 URL，同时在 catalog DB 写入一条 pending 记录。 |

关键安全：
- 不允许客户端任意指定 key 前缀，必须由服务端按 `data_type + platform + version + date + uid + kind + uuid + extension` 拼出后返回。
- presigned URL TTL = 1 小时。

## 3. 文件命名 / 路径

固定使用 `sdk-logs` 桶，按 `kind`（log | dump）子目录区分：

```
s3://sdk-logs/
  {data_type}/
    {platform}/
      {version}/
        {yyyy-mm-dd}/
          {uid}/
            log/{HH-mm-ss}-{uuid}.zip
            dump/{HH-mm-ss}-{uuid}.zip
```

字段说明：
- `data_type`: `cdc` | `cds`
- `platform`: `windows` | `mac` | `linux` | `android` | `ios`
- `version`: SDK 版本号（如 `1.0.0`）
- `yyyy-mm-dd`: 上传日期精度到天
- `uid`: 设备/用户唯一标识
- `kind`: `log` | `dump`
- `HH-mm-ss`: 文件生成时间，精确到秒（如 `14-30-25`）
- `uuid`: 短 UUID（避免碰撞）

## 4. catalog DB（轻量）

Postgres 一张表：

```sql
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

CREATE INDEX ON artifacts (kind, state, occurred_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX ON artifacts (platform, app_release, occurred_at DESC);
CREATE INDEX ON artifacts (uid, occurred_at DESC);
CREATE INDEX ON artifacts (crash_signature) WHERE crash_signature IS NOT NULL;
CREATE INDEX ON artifacts USING GIN (meta);
```

**所有 Web 查询都打这张表**，再按需去 MinIO 取 object，避免对象存储 list 大目录。

## 5. 崩溃聚合视图

```sql
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
```

直接通过 SQL 聚合相同 crash_signature 的 dump，无需依赖额外工具。

## 6. 客户端容错

- 上传失败 → 本地队列重试（指数退避，最长 24 小时），磁盘上限 100 MB（旧的覆盖）。
- 网络受限 / WiFi-only 策略由 SDK option 控制。

## 7. 测试与验收

- 提供 `demo/scripts/seed_data.py`：模拟终端，写入 40 条测试数据到 `sdk-logs` 桶（20 log + 20 dump）。
- `demo/check_data.ps1` / `demo/check_db.sql` / `demo/check_minio.ps1` 提供数据验证。

> 一句话：**当前版本直接上传下载原始文件，加密链路留待后续版本补齐**。