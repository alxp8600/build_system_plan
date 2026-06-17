# 07 Web 查询与下载（对应核心问题 4）

> 一句话：**Filebrowser/MinIO Console 用来"浏览/下载文件"，自写的 `decrypt-proxy` 负责"查询 + 流式透传下载"**。当前 demo 暂不做解密，直接从 MinIO 读取原文件返回。
> 
> ⚠️ **当前 demo 版本暂未启用解密**：decrypt-proxy 目前只做透传下载，不执行 AES-GCM 解密或 zstd 解压。加解密链路留待后续版本补齐。

## 1. 三层 UI 怎么分工

| 角色 | 用户 | 能干什么 |
| --- | --- | --- |
| **MinIO Console** (`minio.intra`) | 管理员 | 看所有 bucket / 改策略 / 看事件 / 下载文件。 |
| **Filebrowser** (`files.intra`) | 开发 / 测试 | 浏览只读视图（挂 MinIO 为后端），按目录树点击下载文件。 |
| **decrypt-proxy 自带页面** (`decrypt.intra`) | 开发 / 测试 | 1) 列表搜索（按 product / channel / release / device / time / crash_signature 查 catalog DB） 2) 点击下载文件流式透传 3) 崩溃聚合查询（dump_groups 视图）。 |

> 三件套同时跑，互不打扰。

## 2. decrypt-proxy 设计（自写，~100 行 FastAPI）

### 2.1 启动

```
SESSION_SECRET=...
S3_ENDPOINT=http://minio:9000
S3_AK / SK = (minio 凭据)
DB_URL=postgresql://...
```

### 2.2 路由

| Method/Path | 说明 |
| --- | --- |
| `GET  /`                       | SPA 入口（Vue/SvelteKit/HTMX 均可，本质就是查询 + 列表） |
| `GET  /api/search`             | 查询 catalog，支持参数：`kind, product, channel, app_release, device_id, crash_signature, page, size` |
| `GET  /api/dump-groups`        | 崩溃聚合查询（基于 `dump_groups` 视图） |
| `GET  /api/download`           | 从 MinIO 读取对象并流式返回，支持参数 `bucket, key` |
| `GET  /healthz`                | 健康检查 |

### 2.3 下载透传实现要点

```python
@app.get("/api/download")
def download(bucket: str, key: str):
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read()
    content_type = obj.get("ContentType", "application/octet-stream")

    # 更新 state 为 uploaded
    db.execute("UPDATE artifacts SET state='uploaded', uploaded_at=now() WHERE bucket=%s AND object_key=%s", (bucket, key))

    filename = key.split("/")[-1]
    return StreamingResponse(
        iter([body]),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
```

要点：
- **简单透传**：`s3.get_object` → 读取 body → `StreamingResponse` 直接返回，不做任何数据变换。
- catalog DB 中 `state` 字段用于跟踪文件是否已从 pending 变为 uploaded。

## 3. Web 列表页（最小可用）

页面只需要 4 个：

1. **登录**（OIDC redirect）
2. **首页 / 仪表盘**：版本动态 + 最新崩溃 sig top10。
3. **日志查询**：表单 = product / channel / release / device / 时间范围。结果表每行有 "下载文件" 按钮 → 调 `/api/download`。
4. **Dump 查询**：默认按 crash_signature 折叠（点开展开同 sig 的所有 dump），同样有下载按钮。

技术栈：**HTMX + Tailwind + Jinja2**（静态资源 < 200KB，跟随 decrypt-proxy 一起 Docker 化），不需要 Node/Vite 也不需要前后端分离。团队偏前端的话也可 Vue 3 + Vite。

## 4. 单条 dump 流程示例

```
开发(浏览器):
   登录 → 输入 crash_signature → 列表显示 23 条 → 点最新一条
        → "下载" 按钮 → 浏览器开始下载 dump 文件
   背后:
     decrypt-proxy:
        1. 校 OIDC token
         2. boto3 GET s3://sdk-logs/...../xxx.zip (流)
        3. StreamingResponse 输出
   开发:
     拿 dmp + 本地 lldb + Nexus 上 metadata 里 sentry_release 找到 .dSYM
        → 调栈搞定
```

## 5. 大文件、海量 list 的性能

- catalog DB 索引足够，单次查询 < 50ms。
- 下载链路：proxy → MinIO 内网千兆，单流 ~100MB/s。
- 不要在网页上做"目录 list" —— MinIO list 超过 1000 对象就开始抖。所有页面都打 catalog DB。


> 一句话：**当前版本直接透传下载原文件，加解密链路留待后续版本补齐**。
