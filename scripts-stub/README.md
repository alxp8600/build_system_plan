# scripts-stub —— 可直接复制改造的最小骨架

> 这里**不是**完整工程，而是 `11-需要自己写的脚本清单.md` 里 4 个自研模块的"种子代码"。每个文件都能独立 review，连起来就构成一套可用的最小系统。
>
> ⚠️ **当前 demo 版本暂未启用加解密**：decrypt-proxy 仅做透传下载，client-tools 不包含加密/解密/密钥生成代码。下文不再包含加密格式说明和 keyring.json 示例。加解密链路留待后续版本补齐。

## 文件索引

| 文件 | 对应模块 | 说明 |
| --- | --- | --- |
| `sdkPipeline.groovy` | jenkins-shared-lib | 业务仓库 `Jenkinsfile` 用 1 行调用即可：`sdkPipeline(product:'rtc-sdk', platforms:[...])` |
| `publish.sh`         | 业务仓库 ci/scripts | Jenkins 流水线 publish 阶段调用：把 zip→Nexus、符号→MinIO+Sentry、可选 Maven/Pods |
| `upload_token.py`    | upload-token       | 客户端 `POST /v1/presign` 接口，签发 presigned PUT URL，写 catalog |
| `decrypt_proxy.py`   | decrypt-proxy      | Web 端搜索 + 透传下载（当前不做解密）+ 审计日志 |
| `sql/init.sql`       | catalog DB schema  | 一张 `artifacts` 主表 + 索引 + `dump_groups` 聚合视图 |
| `compose.yaml`       | infra              | 单机一键起：caddy/nexus/jenkins/minio/postgres/redis + 自研服务 |
| `.env.example`       | infra              | 所有秘钥占位 |

> **已移除**：`sdk_decrypt_cli.py`（离线解密工具），留待加密链路启用后补充。

## 与文档的对应关系

```
01-总体架构.md           →   compose.yaml + 各服务在网络中的拓扑
02-技术选型.md           →   compose.yaml 里选用的开源组件
03-服务器与目录结构.md    →   sql/init.sql (DB) + minio bucket 命名 (compose 中 minio-init)
04-Jenkins出包方案.md     →   sdkPipeline.groovy + publish.sh
05-包与符号表管理.md      →   publish.sh (Nexus + MinIO + Sentry 三路上传)
06-日志Dump上传方案.md    →   upload_token.py (服务端) + 客户端 SDK 端示例
08-保留与清理策略.md      →   minio/lifecycles/*.json + catalog 的 deleted_at 字段
09-安全与权限.md          →   decrypt_proxy 的 OIDC + RBAC + audit()
10-部署清单与上线步骤.md  →   compose.yaml + .env.example 是 day-1 的全部入口
11-需要自己写的脚本清单.md →   本目录就是清单的"种子实现"
```

## 当前 demo 版本的代码范围

decrypt-proxy 当前版本仅包含以下能力：
- 搜索 catalog DB（按 product/channel/device_id/crash_signature）
- 透传下载（`s3.get_object` → `StreamingResponse`，不经过解密/解压）
- OIDC 登录 + RBAC
- JSONL 审计日志

**不包含**：
- AES-GCM 流式解密
- zstd 流式解压
- 密钥管理（keyring.json 加载/轮转）
- Sentry 符号化代理
- 本地离线解密 CLI

以上功能留待后续版本补齐。

## 上线 5 步

```bash
# 0. 准备
cp .env.example .env  &&  vi .env
mkdir -p caddy jenkins/casc minio/{lifecycles,policies} sql

# 1. 起栈
docker compose up -d

# 2. 初始化 Nexus repos / Jenkins JCasC / OIDC 注册
#    (各组件首启会读 jenkins/casc/jenkins.yaml, 由你按 03-/04- 文档填好)

# 3. 业务 SDK 仓库写一个 Jenkinsfile:
cat > Jenkinsfile <<'EOF'
@Library('sdk-shared-lib@main') _
sdkPipeline(
  product:  'rtc-sdk',
  platforms:['android','ios','windows','linux'],
  notify:   [feishu: env.FEISHU_WEBHOOK],
)
EOF

# 4. 提交一次 commit, 看 Jenkins 自动构建 + 包落 Nexus + 符号到 Sentry
```

## 我没有放进来的（自行补齐，<1 天工作量）

- `decrypt-proxy/templates/*.html` 几个 Jinja2 模板（搜索表单 + 列表 + 详情）
- `minio/lifecycles/sdk-logs.json` 等 2 份生命周期 JSON（按 `08-` 文档）
- `minio/policies/*.json` 3 份 IAM 策略（按 `09-` 文档）
- `jenkins/casc/jenkins.yaml` 一份 JCasC（按 `04-` 文档）
- `caddy/Caddyfile` 反代 + TLS + forward_auth（按 `09-` 文档）
- 客户端 SDK 端的「压缩 → 上传」实现（Kotlin/Swift/C++ 三份，~100 行/份）

> 这些都是配置文件级别的体力活，按对应文档抄 / 改即可，不再列入"自研代码"统计。


> 当前 demo 不使用加密格式，客户端直接上传原始 zip 文件。
