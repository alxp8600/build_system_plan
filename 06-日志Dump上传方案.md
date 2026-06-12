# 06 日志 / Dump 上传方案（对应核心问题 3、4 上半）

> 一句话：**客户端 SDK 本地"压缩 → 加密 → 命名" → 向 `upload-token-service` 换一次性 presigned URL → PUT 到 MinIO**。服务端不解密、不解压，**密钥永远不在客户端持久化**。

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
   logs/*.log → zstd 压缩 → AES-256-GCM 加密 → 文件名规范
   dump/*.dmp → 直接 AES-256-GCM 加密 (dump 已是二进制, 不再 zstd)
   ─────────────────────────────────────────
   client 调用:
        POST https://token.intra/v1/upload
        Header: Authorization: Bearer {device_jwt}
        Body  : { product, device_id, kind, key, size, sha256 }
   ←  服务端返回:
        { url: "https://s3.intra/sdk-logs/xxxxx?X-Amz-Signature=...",
          headers: { "x-amz-meta-key-id": "kr-2026-06" } }
   ─────────────────────────────────────────
   PUT 文件流 → MinIO （直连，不过应用服务器）
   ─────────────────────────────────────────
   再调:
        POST https://token.intra/v1/upload/ack
        Body: { key, etag, sha256 }
   服务端确认 → 写入 catalog DB （供 Web 查询）
```

**核心收益**：
- 应用服务器只签 URL，不过流量 → 上百 GB 日志也不会压垮。
- 客户端没有 S3 access key，无法越权访问别人的目录。
- 服务端不接触明文，密钥不下发到端。

## 2. 客户端 SDK 端逻辑（伪代码）

```cpp
struct UploadCtx {
    string product;       // "rtc-sdk"
    string device_id;     // 持久化 uuid
    string app_version;
    string sdk_version;   // = release name
    string session_id;
};

void upload_logs(UploadCtx& ctx, vector<File> logs) {
    auto bundle = zstd_compress(concat(logs));          // 1
    auto key_id = current_key_id();                     // "kr-2026-06"
    auto dek    = derive_dek(ctx.device_id, key_id);    // HKDF, 后述
    auto cipher = aes_gcm_encrypt(bundle, dek);         // 2
    string name = fmt("{seq:04}-{ts}.log.zst.enc", ...);
    string key  = fmt("{product}/{yyyy}/{mm}/{dd}/{device}/{session}-{name}",
                      ctx, name);

    auto resp = http_post("https://token.intra/v1/upload",
        jwt_header(),
        {"product": ctx.product, "kind": "log",
         "key": key, "size": cipher.size(),
         "sha256": sha256(cipher),
         "key_id": key_id,
         "release": ctx.sdk_version,
         "channel": ctx.channel});

    http_put(resp.url, cipher, resp.headers);           // 直传 MinIO
    http_post("https://token.intra/v1/upload/ack",
              jwt_header(),
              {"key": key, "etag": resp.etag,
               "sha256": sha256(cipher)});
}
```

## 3. 加密细节

### 3.1 算法

- 对称：**AES-256-GCM**（12 字节 nonce + 16 字节 tag）。每个文件独立 nonce（随机 12B，写在文件头）。
- 密钥派发：**HKDF-SHA256(master_key_v{N}, salt=device_id, info="sdk-log|sdk-dump")**。
- master_key 仅存在于：
  - decrypt-proxy 服务器内存（启动从 `keyring.json` 读）；
  - 客户端**首次激活**时由 `upload-token-service` 通过 TLS 一次性下发，写入 OS keystore（iOS Keychain / Android Keystore / Windows DPAPI）—— 不写普通文件。
- key rotation：每 6 个月发布新版本 master_key（`kr-2026-06` → `kr-2026-12`），文件头记录 `key_id`；老 key 永久保留于 decrypt-proxy 用于历史文件解密。

### 3.2 文件格式

```
[ magic 4B "SDKL" ]
[ version 1B = 1 ]
[ key_id 12B ASCII, 不足补 0 ]
[ nonce  12B random ]
[ ciphertext ... ]
[ tag    16B ]
```

dump 文件 magic 改为 `"SDKD"`，结构一致。

> 选 AES-GCM 是因为：边写边算 tag、零依赖 OpenSSL/BoringSSL/CryptoKit；解密时如果 tag 不对，proxy 直接 400，避免污染数据。

## 4. 文件命名 / 路径

详见 03 章 bucket 布局。再列一次关键字段：

- 日志（`sdk-logs`）：
  ```
  {product}/{yyyy}/{mm}/{dd}/{device_id}/{session_id}-{seq:04}.log.zst.enc
  ```
- Dump（`sdk-dumps`）：
  ```
  {product}/{sig[:2]}/{sig}/{yyyymmdd-HHMMSS}-{device_id}.dmp.enc
  {product}/{sig[:2]}/{sig}/{yyyymmdd-HHMMSS}-{device_id}.meta.json   # 明文
  ```
  `sig` = crash signature（线程 0 顶端函数 + 平台 + arch 的 sha1 前 12 位），由 SDK 在本地计算，便于在网页上把"同种 crash"折叠成一个目录。

`meta.json` 字段：
```json
{
  "product"  : "rtc-sdk",
  "device_id": "u-9f8a...",
  "app_id"   : "com.foo.bar",
  "app_ver"  : "3.4.1",
  "sdk_ver"  : "1.7.0",
  "channel"  : "release",
  "platform" : "ios",
  "os"       : "iOS 17.5",
  "arch"     : "arm64",
  "ts"       : "2026-06-12T07:11:23Z",
  "crash_signature": "ab12...",
  "release"  : "rtc-sdk@1.7.0+a1b2c3d",
  "sentry_event_id": "..."   // 可选，关联 Sentry
}
```

## 5. upload-token-service（自写 ~50 行 FastAPI）

接口：

| Method/Path | 说明 |
| --- | --- |
| `POST /v1/auth/activate` | 客户端首次激活，校验 app + device 签名后返回设备 JWT + master_key 的派生材料。 |
| `POST /v1/upload` | 校验 JWT，校验 key 路径前缀必须为 `{product}/{device_id}/...`，调用 `boto3.generate_presigned_url('put_object', ExpiresIn=300)` 返回。 |
| `POST /v1/upload/ack` | 客户端上传完成后回传 etag/sha256，server 端做 HEAD 校验 + 写 catalog。 |

关键安全：
- 不允许客户端任意指定 key 前缀，必须由服务端按"product + device_id + 时间"重新拼一遍后返回。
- 上传请求带速率限制：每设备 100 MB / 10 分钟（Redis token bucket）。
- presigned URL TTL = 5 分钟。

## 6. catalog DB（轻量）

Postgres 一张表：

```sql
CREATE TABLE artifacts (
  id           BIGSERIAL PRIMARY KEY,
  kind         TEXT NOT NULL,                  -- 'log' | 'dump'
  product      TEXT NOT NULL,
  channel      TEXT,
  release      TEXT,                           -- 'rtc-sdk@1.7.0+a1b2c3d'
  device_id    TEXT,
  app_version  TEXT,
  platform     TEXT,
  os           TEXT,
  arch         TEXT,
  crash_signature TEXT,
  bucket       TEXT NOT NULL,
  key          TEXT NOT NULL,
  size_bytes   BIGINT,
  sha256       TEXT,
  uploaded_at  TIMESTAMPTZ DEFAULT now(),
  encrypted    BOOL DEFAULT TRUE,
  key_id       TEXT,
  meta         JSONB
);
CREATE INDEX ON artifacts (product, channel, release, uploaded_at DESC);
CREATE INDEX ON artifacts (crash_signature);
CREATE INDEX ON artifacts (device_id, uploaded_at DESC);
```

**所有 Web 查询都打这张表**，再按需去 MinIO 取 object，避免对象存储 list 大目录。

## 7. 客户端容错

- 上传失败 → 本地队列重试（指数退避，最长 24 小时），磁盘上限 100 MB（旧的覆盖）。
- 网络受限 / WiFi-only 策略由 SDK option 控制。
- 防止重复：客户端把 `sha256` 当 idempotency key，服务端 `ack` 时如果 catalog 已存在同 sha256 → 标记为重复并删除新 object（MinIO 删开销小）。

## 8. 测试与验收

- 提供 `client-tools/upload_test.py`：模拟终端，造一个 100 MB 日志，走完整流程；附 `--bad-tag` / `--wrong-key-id` 用于解密失败路径回归。
- Jenkins 加一个夜间任务跑该脚本，对 staging 环境做"上传 + 网页解密下载 + sha 校验"，结果不通过则告警。

## 9. 一句话总结

> **SDK 本地加密、服务端只发短期 URL、文件直传 MinIO、catalog 记元数据**。流量绕开应用服务器、密钥不下发明文、客户端无任何长期凭证 —— 量级再大也能扛。