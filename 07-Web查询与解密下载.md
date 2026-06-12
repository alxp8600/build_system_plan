# 07 Web 查询与解密下载（对应核心问题 4）

> 一句话：**Filebrowser/MinIO Console 用来"浏览/下载密文"，自写的 `decrypt-proxy` 负责"按需解密 + 解压 + 流式下载"**，前端只调用 proxy，密钥永不进浏览器。

## 1. 三层 UI 怎么分工

| 角色 | 用户 | 能干什么 |
| --- | --- | --- |
| **MinIO Console** (`minio.intra`) | 管理员 | 看所有 bucket / 改策略 / 看事件 / 下载密文。 |
| **Filebrowser** (`files.intra`) | 开发 / 测试 | 浏览只读视图（挂 MinIO 为后端），按目录树点击下载密文，或点击"解密下载"按钮（跳到 decrypt-proxy）。 |
| **decrypt-proxy 自带页面** (`decrypt.intra`) | 开发 / 测试 | 1) 列表搜索（按 product / channel / release / device / time / crash_signature 查 catalog DB） 2) 点击"明文下载"流式解密 dump/log 3) 点击"在线预览"前 1MB 文本流式解密返回。 |

> 三件套同时跑，互不打扰；**只有 decrypt-proxy 持有密钥**。

## 2. decrypt-proxy 设计（自写，~300 行 FastAPI）

### 2.1 启动

```
DECRYPT_PROXY_KEYRING=/srv/sdk-portal/decrypt-proxy/keyring.json   # 600 权限
DECRYPT_PROXY_OIDC_ISSUER=https://gitea.intra/                     # 复用 Gitea 账号
DECRYPT_PROXY_AUDIT_LOG=/var/log/decrypt-proxy/audit.jsonl
S3_ENDPOINT=https://s3.intra
S3_INTERNAL_AK / SK = (只读 IAM 用户)
DB_URL=postgresql://...
```

`keyring.json`：
```json
{
  "kr-2026-06": "BASE64_32B",
  "kr-2026-12": "BASE64_32B"
}
```

### 2.2 路由

| Method/Path | 说明 |
| --- | --- |
| `GET  /`                       | SPA 入口（Vue/SvelteKit/HTMX 均可，本质就是查询 + 列表） |
| `GET  /api/search`             | 查询 catalog，支持参数：`kind, product, channel, release, device_id, crash_signature, start, end, q, limit, cursor` |
| `GET  /api/detail/{id}`        | 单条 artifact 详情 |
| `GET  /d/raw/{id}`             | 直接 302 到 MinIO presigned URL（下载密文原文，做取证） |
| `GET  /d/plain/{id}`           | **流式解密下载明文**，自动按 magic 判断 SDKL / SDKD |
| `GET  /d/preview/{id}?head=64k`| 解密前 N 字节，做日志在线预览（dump 不支持） |
| `POST /api/symbolicate/{id}`   | （dump 用）转 Sentry minidump endpoint 或本地 `minidump_stackwalk` + symbol 目录 |
| `GET  /healthz`                | 健康检查 |
| `GET  /audit`                  | （admin 角色）审计日志查询 |

### 2.3 解密流式实现要点

```python
@app.get("/d/plain/{id}")
def plain(id: int, user = Depends(require_oidc)):
    art = db.get(id)
    require_role(user, art)               # 见 09 章 RBAC
    audit(user, "download_plain", art)

    s3_stream = s3.get_object(Bucket=art.bucket, Key=art.key)["Body"]
    header     = s3_stream.read(4 + 1 + 12 + 12)
    magic, ver, kid, nonce = parse_header(header)
    key = keyring[kid.decode().strip("\x00")]

    def gen():
        decryptor = AESGCM(key).decryptor_streaming(nonce)
        # 边读边解, 同时拆出末尾 16B tag 留到最后 verify
        # 真正实现用 cryptography.hazmat 低级 API + 16B 尾窗口
        for chunk in stream_with_tail(s3_stream, tail=16):
            plain = decryptor.update(chunk)
            if magic == b"SDKL":
                plain = zstd_decoder.decompress(plain)
            yield plain
        decryptor.finalize_with_tag(tail_bytes)   # tag 不对就抛, 关流

    fn = derive_filename(art)
    return StreamingResponse(
        gen(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{fn}"'},
    )
```

要点：
- **真正流式**，不要 `read all → decrypt → return`，否则 2 GB 日志会撑爆内存。
- AES-GCM 的 tag 在末尾 16 字节，实现时维护一个 16B 尾窗口；tag 校验在 finalize 时进行；任何篡改/坏 key 会直接抛 → HTTP 500 + 审计 fail。
- zstd 流式解压用 `zstandard.ZstdDecompressor.stream_reader()` 包到 GCM 解密后的迭代器上。

### 2.4 安全护栏

- **OIDC 登录**：所有路由 require auth；用 Gitea 自带 OIDC Provider 即可。
- **RBAC**：
  - `viewer`：可看列表、可下载明文 dev/staging。
  - `senior`：另可下载 release 通道明文。
  - `admin`：可下载 + 改 keyring + 看审计。
- **审计日志**（强制）：每次解密下载写一行 JSONL：`ts, user, ip, id, key, kind, channel, release, action`。
- **速率限制**：单用户 60 次/分钟、单 IP 200 次/分钟。
- **下载水印**（可选）：明文下载时在文件头追加一行 `# decrypted by {user} at {ts} from {id}`（仅 log，dump 不动）。
- **密钥保护**：keyring.json 文件 mode 600，owner=decrypt-proxy 进程用户；推荐再用 `systemd-creds` 或 Vault Agent 注入到环境变量，启动后从内存清掉文件。

## 3. Web 列表页（最小可用）

页面只需要 4 个：

1. **登录**（OIDC redirect）
2. **首页 / 仪表盘**：版本动态 + 最新崩溃 sig top10。
3. **日志查询**：表单 = product / channel / release / device / 时间范围 / 关键字（关键字仅查 meta，明文内容不在数据库里）。结果表每行有 "下载密文 / 下载明文 / 预览前 64KB"。
4. **Dump 查询**：默认按 crash_signature 折叠（点开展开同 sig 的所有 dump），同样三个按钮，外加 "符号化分析" → 调 `/api/symbolicate/{id}` 拉 Sentry / 本地 stackwalk 结果。

技术栈：**HTMX + Tailwind + Jinja2**（静态资源 < 200KB，跟随 decrypt-proxy 一起 Docker 化），不需要 Node/Vite 也不需要前后端分离。团队偏前端的话也可 Vue 3 + Vite。

## 4. 单条 dump 流程示例

```
开发(浏览器):
   登录 → 输入 crash_signature=ab12... → 列表显示 23 条 → 点最新一条
        → "下载明文" 按钮 → 浏览器开始下载 ab12-2026...dmp
   背后:
     decrypt-proxy:
        1. 校 OIDC token
        2. 查 catalog id=12345
        3. boto3 GET s3://sdk-dumps/...../12345.dmp.enc (流)
        4. 流式 AES-GCM 解密
        5. StreamingResponse 输出
        6. 写一行 audit
   开发:
     拿 dmp + 本地 lldb + Nexus 上 metadata.json 里 sentry_release 找到 .dSYM
        → 调栈搞定; 或直接点 "符号化分析" 让 proxy 调 Sentry minidump endpoint
        返回 JSON 调栈直接展示在网页。
```

## 5. 大文件、海量 list 的性能

- catalog DB 索引足够，单次查询 < 50ms。
- 下载链路：proxy → MinIO 内网千兆，单流 ~100MB/s；2GB 文件解密下载 < 30 秒。
- 不要在网页上做"目录 list" —— MinIO list 超过 1000 对象就开始抖。所有页面都打 catalog DB。

## 6. 离线 / 紧急通道

万一 decrypt-proxy 挂了：
```bash
# 1. 任一管理员主机上
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
mc cp myminio/sdk-dumps/...../ab12....dmp.enc ./
python3 client-tools/sdk_decrypt_cli.py \
       --keyring /etc/sdk/keyring.json \
       --in ab12....dmp.enc --out ab12....dmp
```

`sdk_decrypt_cli.py` 是 decrypt-proxy 同一份解密逻辑的 50 行包装版，长期与 proxy 同步。

## 7. 一句话总结

> **密钥只在 decrypt-proxy 内存里；浏览器永远拿不到密钥；解密、解压都在 proxy 流式完成；下载都被审计。** 想看密文 → MinIO Console / Filebrowser；想看明文 → decrypt-proxy。