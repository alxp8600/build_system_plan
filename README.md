# 音视频 SDK 出包 + 日志/Dump 管理系统 — 搭建方案（非自研）

> 目标：用**现成成熟的开源/商业组件**拼出一套完整系统，团队只写少量"胶水脚本"，3-5 人小团队 1-2 周可上线。
>
> 适用场景：中小团队的音视频 SDK，多平台（Android / iOS / Windows / macOS / Linux），需要：
> 1. Git 提交即触发多平台编译出包；
> 2. 包 / 符号表 / 提测版本 / 线上版本 / 日志 / dump 集中管理；
> 3. 客户端 SDK 把加密日志/dump 上传到服务器指定目录；
> 4. 网页查询、下载、自动解密+解压、定位问题。

## 目录

| 文件 | 内容 |
| ---- | ---- |
| [`01-总体架构.md`](01-总体架构.md) | 一张大图 + 数据流；明确各组件分工。 |
| [`02-技术选型.md`](02-技术选型.md) | 每个能力点的候选方案对比与最终推荐（含轻量版/标准版/扩展版）。 |
| [`03-服务器与目录结构.md`](03-服务器与目录结构.md) | 服务器规格、存储方案（NFS / MinIO）、目录与命名规范。 |
| [`04-Jenkins出包方案.md`](04-Jenkins出包方案.md) | Jenkins 是否合适、插件清单、Pipeline 模板、Webhook 接入、签名/凭据管理。 |
| [`05-包与符号表管理.md`](05-包与符号表管理.md) | 用什么仓库管什么包（Nexus/Generic + Sentry 符号表 + Git LFS 等），提测/线上版本流转。 |
| [`06-日志Dump上传方案.md`](06-日志Dump上传方案.md) | 客户端"加密+压缩"格式选型、传输通道（MinIO presigned / HTTPS）、服务端落盘目录。 |
| [`07-Web查询与解密下载.md`](07-Web查询与解密下载.md) | 选 Filebrowser / MinIO Console / Sentry / 自研薄壳的取舍；如何在网页"一键解密下载"。 |
| [`08-保留与清理策略.md`](08-保留与清理策略.md) | 各类数据保留时长、自动清理（MinIO Lifecycle / cron）。 |
| [`09-安全与权限.md`](09-安全与权限.md) | 账号体系（Jenkins / Nexus / MinIO / Sentry / Web）、密钥管理、HTTPS、审计。 |
| [`10-部署清单与上线步骤.md`](10-部署清单与上线步骤.md) | 一份"按顺序点开就能搭好"的 checklist + docker-compose 骨架。 |
| [`11-需要自己写的脚本清单.md`](11-需要自己写的脚本清单.md) | 唯一需要团队自己开发的胶水脚本（很少），明确边界。 |
| [`scripts-stub/`](scripts-stub/) | 关键胶水脚本的最小可用骨架（不是完整产品代码）。 |

## TL;DR — 一句话方案

> **Gitea/GitHub + Jenkins + Nexus(包) + Sentry(符号表/Crash) + MinIO(日志/Dump 原始文件) + Filebrowser(网页浏览/下载) + 一个 ~200 行的 Python 解密服务 + 一份 Jenkins Shared Library**。

各能力点对应组件：

| 需求 | 选用 |
| --- | --- |
| Git 托管 | Gitea（自建）或 GitHub/GitLab（已有就用） |
| 触发编译 | **Jenkins** + Generic Webhook Trigger |
| 多平台构建 | Jenkins 多 agent（Win / macOS / Linux）+ Docker buildx |
| SDK 二进制包仓库 | **Sonatype Nexus 3 (Raw / Maven / Cocoapods / npm)** |
| 符号表 + Crash 聚合 | **Sentry（自建 self-hosted）** |
| 提测/线上版本流转 | Nexus 仓库分层（dev/staging/release）+ Jenkins 审批 stage |
| 日志/Dump 原始存储 | **MinIO（S3 兼容，单机或 4 节点 EC）** |
| 客户端上传 | MinIO **预签名 URL**（无需自建上传服务） |
| 网页浏览/下载 | **Filebrowser**（轻量）或 **MinIO Console**（自带） |
| 网页"解密+解压"下载 | 自写一个 ~200 行 Python FastAPI "decrypt-proxy"，对接 MinIO |
| 反向代理/TLS | **Caddy**（自动 HTTPS）或 Nginx |
| 通知 | Jenkins → 飞书/钉钉/企业微信机器人（webhook 直接 POST） |

整个系统的"自研代码量" = **1 个 FastAPI 小服务 + 1 套 Jenkins Shared Library + 几个 shell/python 胶水脚本**，其余全部是现成镜像 `docker compose up -d` 起来。

按顺序读 `01 → 10` 就能照着搭。需要写代码的部分见 `11` 和 `scripts-stub/`。