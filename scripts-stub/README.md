# scripts-stub —— 可直接复制改造的最小骨架

> 这里**不是**完整工程，而是 `11-需要自己写的脚本清单.md` 里 4 个自研模块的"种子代码"。每个文件都能独立 review，连起来就构成一套可用的最小系统。

## 文件索引

| 文件 | 对应模块 | 说明 |
| --- | --- | --- |
| `sdkPipeline.groovy` | jenkins-shared-lib | 业务仓库 `Jenkinsfile` 用 1 行调用即可：`sdkPipeline(product:'rtc-sdk', platforms:[...])` |
| `publish.sh`         | 业务仓库 ci/scripts | Jenkins 流水线 publish 阶段调用：把 zip→Nexus、符号→MinIO+Sentry、可选 Maven/Pods |
| `upload_token.py`    | upload-token       | 客户端 `activate / upload / upload/ack` 三接口，签发 presigned PUT URL，写 catalog |
| `decrypt_proxy.py`   | decrypt-proxy      | Web 端搜索 + 流式解密下载 + Sentry 符号化 + 审计日志 |
| `sdk_decrypt_cli.py` | client-tools       | 紧急通道：本地拿 keyring.json 解密单个 dump/log |
| `sql/init.sql`       | catalog DB schema  | 一张 `artifacts` 主表 + 索引 + `dump_groups` 聚合视图 |
| `compose.yaml`       | infra              | 单机一键起：caddy/nexus/jenkins/minio/postgres/redis + 上面 2 个自研服务 |
| `.env.example`       | infra              | 所有秘钥占位 |

## 与文档的对应关系

```
01-总体架构.md           →   compose.yaml + 各服务在网络中的拓扑
02-技术选型.md           →   compose.yaml 里选用的开源组件
03-服务器与目录结构.md    →   sql/init.sql (DB) + minio bucket 命名 (compose 中 minio-init)
04-Jenkins出包方案.md     →   sdkPipeline.groovy + publish.sh
05-包与符号表管理.md      →   publish.sh (Nexus + MinIO + Sentry 三路上传)
06-日志Dump上传方案.md    →   upload_token.py (服务端) + 客户端 SDK 端示例 (待业务侧实现)
07-Web查询与解密下载.md   →   decrypt_proxy.py
08-保留与清理策略.md      →   minio/lifecycles/*.json (待补) + catalog 的 deleted_at 字段
09-安全与权限.md          →   keyring.json + decrypt_proxy.require_can_read + audit()
10-部署清单与上线步骤.md  →   compose.yaml + .env.example 是 day-1 的全部入口
11-需要自己写的脚本清单.md →   本目录就是清单的"种子实现"
```

## 加密格式（务必三端一致）

```
file := header || ciphertext || tag
header (29B) := magic(4) | ver(1) | key_id(12, ascii \0-padded) | nonce(12)
magic        := "SDKL" -> log 通道, plaintext 是 zstd 流
             := "SDKD" -> dump 通道, plaintext 是原始 minidump
cipher       := AES-256-GCM, key = keyring[key_id], aad = header
tag          := 16B GCM tag (写在末尾)
```

> 同样的常量在：`decrypt_proxy.py`、`sdk_decrypt_cli.py`、客户端 SDK 三处都要严格一致。
> 客户端 SDK 端代码不在本目录，由业务侧按各平台语言实现（参考 `client-tools/sdk_client_example/`）。

## keyring.json 示例

```json
{
  "k20251201": "Base64(32B-Key-1)==",
  "k20260301": "Base64(32B-Key-2)=="
}
```

- 每季度生成 1 把新 key，`key_id` 写入 header；
- 旧 key 永不删除，只是不再用于加密；
- 文件由 SRE 管理，挂载为 docker secret/ro 卷：`/secrets/keyring.json`。

## 上线 5 步

```bash
# 0. 准备
cp .env.example .env  &&  vi .env
mkdir -p secrets caddy jenkins/casc minio/{lifecycles,policies} sql

# 1. 写好 keyring.json (至少 1 把 key)
python3 -c 'import os,base64,json;print(json.dumps({"k$(date +%Y%m)":base64.b64encode(os.urandom(32)).decode()},indent=2))' > secrets/keyring.json

# 2. 起栈
docker compose up -d

# 3. 初始化 Nexus repos / Jenkins JCasC / OIDC 注册
#    (各组件首启会读 jenkins/casc/jenkins.yaml, 由你按 03-/04- 文档填好)

# 4. 业务 SDK 仓库写一个 Jenkinsfile:
cat > Jenkinsfile <<'EOF'
@Library('sdk-shared-lib@main') _
sdkPipeline(
  product:  'rtc-sdk',
  platforms:['android','ios','windows','linux'],
  notify:   [feishu: env.FEISHU_WEBHOOK],
)
EOF

# 5. 提交一次 commit, 看 Jenkins 自动构建 + 包落 Nexus + 符号到 Sentry
```

## 我没有放进来的（自行补齐，<1 天工作量）

- `decrypt-proxy/templates/*.html` 几个 Jinja2 模板（搜索表单 + 列表 + 详情）
- `minio/lifecycles/sdk-logs.json` 等 3 份生命周期 JSON（按 `08-` 文档）
- `minio/policies/*.json` 3 份 IAM 策略（按 `09-` 文档）
- `jenkins/casc/jenkins.yaml` 一份 JCasC（按 `04-` 文档）
- `caddy/Caddyfile` 反代 + TLS + forward_auth（按 `09-` 文档）
- 客户端 SDK 端的「压缩 → 加密 → 上传」实现（Kotlin/Swift/C++ 三份，~150 行/份）

> 这些都是配置文件级别的体力活，按对应文档抄 / 改即可，不再列入"自研代码"统计。