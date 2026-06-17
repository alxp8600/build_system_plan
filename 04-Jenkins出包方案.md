# 04 Jenkins 出包方案（对应核心问题 1）

> 一句话：**Gitea/GitLab webhook → Jenkins Multibranch Pipeline → matrix 平台并行编译 → 写 Nexus / Sentry / 通知**。所有逻辑沉淀在一个 **Shared Library**，业务仓库只放 30 行 `Jenkinsfile`。

## 1. 是否考虑 Jenkins —— 是

理由再列一次（中小团队视角）：

- 学习成本和资料量最大，遇到问题搜得到。
- macOS / iOS agent 最成熟（Xcode、CocoaPods、fastlane 全套）。
- `Shared Library` 让 N 个 SDK 仓库共享同一份模板，仅 `Jenkinsfile` 30 行。
- `input` step 天然支持"提测/发布"双人审批。
- 与 Git 平台解耦（Gitea/GitLab/GitHub 都用 webhook，可随时迁）。

如果团队明确不想要 master 节点（嫌运维重），可改 **GitLab CI + 自建 macOS Runner**，但 iOS Runner 维护成本最终也不轻。

## 2. 整体流水线

```
push / MR  ─►  Gitea/GitLab webhook
                     │
                     ▼
              Jenkins Multibranch
              (job = 仓库名)
                     │
       ┌─────────────┼─────────────┬──────────────┐
       ▼             ▼             ▼              ▼
  [Android]      [iOS]        [Windows]       [Linux]
  build.sh     build.sh      build.cmd       build.sh
  unit-test    unit-test     unit-test       unit-test
       │             │             │              │
       └─────► package.sh (统一打 sdk.zip + symbols.zst) ◄────┘
                     │
                     ▼
              publish.sh
       ├─ Nexus  ( raw + maven )
       ├─ Sentry ( debug files + release )
       └─ MinIO  ( buildlog 备份 )
                     │
                     ▼
            飞书/钉钉通知（含下载链接）
```

## 3. 仓库里的 `Jenkinsfile`（业务方写的全部内容）

```groovy
@Library('sdk-shared-lib@main') _

sdkPipeline(
    product   : 'rtc-sdk',
    platforms : ['android', 'ios', 'windows', 'linux'],
    // 默认分支策略 → 见 Shared Library
    notify    : [
        feishu  : env.FEISHU_WEBHOOK,
        dingtalk: env.DINGTALK_WEBHOOK,
    ]
)
```

仅此一文件。

## 4. Shared Library `vars/sdkPipeline.groovy`（核心 ~150 行）

骨架（完整文件见 `scripts-stub/sdkPipeline.groovy`）：

```groovy
def call(Map cfg) {
    def channel = decideChannel(env.BRANCH_NAME, env.TAG_NAME)
    // channel ∈ ['dev', 'staging', 'release']

    pipeline {
        agent none
        options { timestamps(); buildDiscarder(logRotator(numToKeepStr: '50')) }
        environment {
            PRODUCT   = cfg.product
            CHANNEL   = channel
            BUILD_ID  = "${env.BUILD_NUMBER}"
            VERSION   = readVersion()           // 见下
            NEXUS_REPO= "sdk-raw-${channel}"
        }
        stages {
            stage('Checkout') {
                steps { ... }
            }

            stage('Build matrix') {
                parallel cfg.platforms.collectEntries { p ->
                    ["${p}": {
                        node(p == 'ios' ? 'mac' : (p == 'windows' ? 'win' : 'linux')) {
                            checkout scm
                            sh/bat "ci/scripts/build-${p}.${p=='windows'?'cmd':'sh'}"
                            sh/bat "ci/scripts/package.sh ${p}"
                            stash name: "pkg-${p}", includes: "dist/${p}/**"
                        }
                    }]
                }
            }

            stage('Aggregate') {
                agent { label 'linux' }
                steps {
                    cfg.platforms.each { unstash "pkg-${it}" }
                    sh "ci/scripts/aggregate.sh"   // 合并 manifest, 计算 sha
                }
            }

            stage('Publish dev') {
                when { expression { channel == 'dev' } }
                steps { sh 'ci/scripts/publish.sh dev' }
            }

            stage('Approve → staging') {
                when { expression { channel == 'staging' } }
                steps {
                    input message: '提测到 staging?', submitter: 'qa-leads,tech-leads'
                    sh 'ci/scripts/publish.sh staging'
                }
            }

            stage('Approve → release') {
                when { expression { channel == 'release' } }
                steps {
                    input message: '发布到 release?', submitter: 'release-managers'
                    sh 'ci/scripts/publish.sh release'
                }
            }
        }
        post {
            success { notify(cfg.notify, 'SUCCESS') }
            failure { notify(cfg.notify, 'FAILURE') }
        }
    }
}
```

## 5. 分支与版本号策略

| Git 操作 | channel | 版本号示例 |
| --- | --- | --- |
| 任意分支 push | `dev` | `1.7.0-dev.123+a1b2c3d` （`123` = Jenkins build id） |
| 合到 `release/x.y` 或打 `vX.Y.Z-rc.N` tag | `staging` | `1.7.0-rc.2+a1b2c3d` |
| 打 `vX.Y.Z` tag | `release` | `1.7.0` |

版本号统一在仓库 `VERSION` 文件 + `git describe` 计算；脚本 `readVersion()` 见 `scripts-stub/version.sh`。

**重要**：dev 包永远只进 `sdk-raw-dev`、`sdk-maven-dev`，不可手工往 release 推；release 必须由 release pipeline 经 `input` 审批写入，**且要求 git tag 已签名**（`git tag -s`）。

## 6. webhook 配置

- Gitea：仓库 → Settings → Webhooks → 选 Jenkins → URL：`https://ci.intra/gitea-webhook/post`，事件勾 Push / PR / Tag。
- GitLab：Project → Webhooks → URL：`https://ci.intra/project/<job>`，token 用 Jenkins 端"Secret token"。
- Jenkins 端开 Multibranch + Branch Source（Gitea/GitLab Plugin），扫描间隔 0（依赖 webhook，节省资源）。

## 7. agent 准备

| Agent | 必备 |
| --- | --- |
| Linux | docker, git, ndk r26, cmake, zstd, openjdk-17, gradle, awscli (mc), sentry-cli |
| macOS | Xcode 15+, CocoaPods, fastlane, sentry-cli, mc, zstd |
| Windows | VS BuildTools 2022, cmake, zstd, sentry-cli, mc, git |

> 推荐用 Docker 化构建（Linux/Windows containers），把 toolchain 锁版本，减少"换 agent 就编不过"的痛苦。macOS 没法 docker，用 Xcode 版本切换工具 `xcversion` 锁定。

## 8. 构建产物清单（每平台）

`dist/{platform}/`：
- `sdk.zip`（头文件 + 库文件 + LICENSE）
- `sdk.zip.sha256`
- `symbols/`（dSYM / pdb / .so.sym），由 `package.sh` 收集
- `metadata.json`（git sha、jenkins url、构建者、时间、ndk/xcode 版本…）
- `sbom.cdx.json`（CycloneDX，`syft` 一行生成）
- `buildlog.txt`（`tee` 出来的完整构建日志）

## 9. publish.sh 干的事

```bash
#!/usr/bin/env bash
set -euo pipefail
ch=$1   # dev|staging|release

# 1) 上传 sdk.zip 到 Nexus raw
for p in dist/*/; do
  plat=$(basename "$p")
  curl -u $NEXUS_USER:$NEXUS_PASS --upload-file "$p/sdk.zip" \
       "https://nexus.intra/repository/sdk-raw-$ch/$PRODUCT/$plat/$VERSION-$BUILD_ID/sdk.zip"
done

# 2) Android aar → maven 协议
mvn deploy:deploy-file ...

# 3) 符号表 → Sentry
sentry-cli debug-files upload --org mycompany --project rtc-sdk-$plat \
    --include-sources dist/*/symbols

# 4) 标记 release
sentry-cli releases new "$PRODUCT@$VERSION-$BUILD_ID"
sentry-cli releases set-commits --auto "$PRODUCT@$VERSION-$BUILD_ID"
sentry-cli releases deploys "$PRODUCT@$VERSION-$BUILD_ID" new -e "$ch"

# 5) buildlog 备份到 MinIO
mc cp buildlog.txt myminio/sdk-builds/$PRODUCT/$VERSION-$BUILD_ID/
```

完整脚本见 `scripts-stub/publish.sh`。

## 10. 通知

`notify()` 函数从 cfg 拿 webhook，POST 卡片消息，包括：
- 状态、产品、版本、channel
- Git commit + author
- Jenkins URL
- Nexus 下载 URL（每个平台一行）
- Sentry release URL

## 11. JCasC（配置即代码）

`jenkins/casc/jenkins.yaml` 把以下内容版本化：
- 用户与角色（管理员、release-manager、qa、developer）
- Credentials（Nexus、Sentry、Gitea token、飞书 webhook、code-sign 证书）
- agent label
- Shared Library 配置

Jenkins 启动加 `-DCASC_JENKINS_CONFIG=/var/jenkins_home/casc/jenkins.yaml`。

## 12. CD/CI 双轨方案

实际工程中，**Shared Library + Multibranch Pipeline** 和 **简单 Freestyle Job** 可以共存：

| 场景 | 方式 | 何时用 |
|---|---|---|
| 多平台矩阵（Android/iOS/Windows/Linux） | Shared Library `sdkPipeline()` | 多个 SDK 仓库复用同一模板，30 行 Jenkinsfile 接入 |
| 单平台快速出包（仅 Windows） | Freestyle Job，3 个 batch step | 小团队/单产品，不需要 Shared Library 的复杂度 |

### 12.1 Freestyle Job 示例（cdc-win）

**Jenkins → New Item → Freestyle project**，构建步骤 3 个 Execute Windows batch command：

```cmd
rem Step 1: Build
call projects\windows\build.bat Release

rem Step 2: Package (zip with timestamp in filename)
call projects\windows\package.bat Release

rem Step 3: Upload to MinIO
call projects\windows\upload.bat "" "http://minio.internal:9000" "%MINIO_JENKINS_AK%" "%MINIO_JENKINS_SK%"
```

**文件名传递机制**：
- `package.bat` 成功后将 zip 文件名写入 `package\_last_zip.txt`
- `upload.bat` 参数 1 为空时自动读取该文件，不再执行 `dir /o-d` glob（避免选错文件）
- 也支持显式传入：`upload.bat "package\cdc-win-xxx.zip" ...`

**Jenkins 凭据绑定**（Jenkins → Manage Credentials）：
- `MINIO_JENKINS_AK` / `MINIO_JENKINS_SK` 为 `jenkins-uploader` 策略的 AK/SK
- 在 Freestyle 配置中通过 **Bindings** → **Inject passwords as environment variables** 注入

**MinIO 树形存储路径**（由 `upload.ps1` 自动解析 zip 文件名生成）：

```
cdc/develop/windows/1.0.0/20260616/cdc-win-1.0.0-develop.51+e2de945-20260616-183702.zip
 ^^^  ^^^^^^^  ^^^^^^^   ^^^^^  ^^^^^^^^  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
 产品   分支     平台     版本    日期                        文件名
```

- 分支从版本号 `*-develop.*` / `*-hotfix.*` / `*-rc.*` 中提取
- 日期从文件名时间戳 `YYYYMMDD` 段提取（精度到天）
- 上传前 HEAD 检查，已存在则跳过（幂等，不覆盖）

详细代码参见：
- `projects/windows/build.bat` — cmake 配置 + 编译
- `projects/windows/package.bat` — 提取版本号 + 打包 zip
- `projects/windows/upload.bat` — MinIO 上传 wrapper
- `projects/windows/upload.ps1` — SigV4 签名 + HEAD 检查 + 上传 + 验证 + 删除本地文件

## 13. 一句话总结

> **业务仓库只维护一份 30 行 Jenkinsfile + ci/scripts/build-*.sh，所有"通道、签名、上传、通知"都在 Shared Library 里。** 任何新 SDK 接入 = 复制这两件事即可，30 分钟接入完毕。对于单平台场景，Freestyle + 3 个批处理同样可用，`package.bat → _last_zip.txt → upload.bat` 保证了文件名精确传递。
