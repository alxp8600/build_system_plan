"""
sdk-build-plan/scripts-stub/decrypt_proxy.py

decrypt-proxy  (FastAPI, ~300 lines)
功能：
  - GET  /                列表 + 搜索页（HTMX）
  - GET  /api/search      查 catalog DB
  - GET  /d/raw/{id}      302 到 MinIO presigned, 下载密文
  - GET  /d/plain/{id}    流式 AES-GCM 解密 (+ zstd 解压, 若 log) 后下载明文
  - GET  /d/preview/{id}  前 N 字节明文预览（仅 log）
  - POST /api/symbolicate/{id}  调 Sentry minidump endpoint
  - GET  /audit           （admin）查审计

依赖：
  fastapi uvicorn[standard] boto3 sqlalchemy psycopg[binary]
  cryptography zstandard jinja2 authlib httpx pydantic-settings
"""
from __future__ import annotations

import os, io, json, time, struct, base64, secrets
from typing import Iterator
from datetime import datetime, timezone

import boto3, httpx, zstandard
from botocore.config import Config as BotoConfig
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, Request, Depends, HTTPException, Response, Query
from fastapi.responses import StreamingResponse, RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, text
from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware


# ---------- 配置 ---------------------------------------------------------
class S:
    KEYRING_PATH = os.environ["DECRYPT_PROXY_KEYRING"]          # /keyring.json
    AUDIT_LOG    = os.environ.get("DECRYPT_PROXY_AUDIT_LOG", "/var/log/decrypt-proxy/audit.jsonl")
    SESSION_KEY  = os.environ["SESSION_SECRET"]
    OIDC_ISSUER  = os.environ["DECRYPT_PROXY_OIDC_ISSUER"]      # https://git.intra/
    OIDC_ID      = os.environ["OIDC_CLIENT_ID"]
    OIDC_SECRET  = os.environ["OIDC_CLIENT_SECRET"]
    S3_ENDPOINT  = os.environ["S3_ENDPOINT"]
    S3_AK        = os.environ["S3_AK"]
    S3_SK        = os.environ["S3_SK"]
    DB_URL       = os.environ["DB_URL"]
    SENTRY_DSN   = os.environ.get("SENTRY_MINIDUMP_ENDPOINT", "")
    PREVIEW_MAX  = 64 * 1024


# ---------- 全局 ---------------------------------------------------------
app = FastAPI(title="decrypt-proxy")
app.add_middleware(SessionMiddleware, secret_key=S.SESSION_KEY, https_only=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

engine = create_engine(S.DB_URL, pool_pre_ping=True, future=True)

s3 = boto3.client(
    "s3", endpoint_url=S.S3_ENDPOINT,
    aws_access_key_id=S.S3_AK, aws_secret_access_key=S.S3_SK,
    config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
)

with open(S.KEYRING_PATH, "rb") as f:
    KEYRING: dict[str, bytes] = {k: base64.b64decode(v) for k, v in json.load(f).items()}

oauth = OAuth()
oauth.register(
    "sso",
    client_id=S.OIDC_ID, client_secret=S.OIDC_SECRET,
    server_metadata_url=f"{S.OIDC_ISSUER}.well-known/openid-configuration",
    client_kwargs={"scope": "openid profile email groups"},
)


# ---------- 认证 / RBAC --------------------------------------------------
def current_user(req: Request) -> dict:
    u = req.session.get("user")
    if not u:
        raise HTTPException(401, "login required")
    return u

def require_can_read(user: dict, channel: str):
    groups = set(user.get("groups", []))
    if "sre" in groups or "admin" in groups: return
    if channel in ("dev", "staging") and (groups & {"developers", "qa"}): return
    if channel == "release"          and (groups & {"release-managers", "qa"}): return
    raise HTTPException(403, "no permission")

def require_admin(user: dict):
    if not (set(user.get("groups", [])) & {"sre", "admin"}):
        raise HTTPException(403, "admin only")


# ---------- 审计 ---------------------------------------------------------
def audit(user: dict, action: str, art_row=None, **extra):
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": user.get("login") or user.get("email"),
        "groups": user.get("groups", []),
        "action": action,
    }
    if art_row is not None:
        line.update(id=art_row.id, bucket=art_row.bucket, key=art_row.object_key,
                    kind=art_row.kind, channel=art_row.channel,
                    release=art_row.app_release, sha256=art_row.sha256)
    line.update(extra)
    with open(S.AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


# ---------- 解密核心 -----------------------------------------------------
HEADER_FMT = "<4sB12s12s"      # magic(4) ver(1) key_id(12) nonce(12)  = 29B
HEADER_SZ  = struct.calcsize(HEADER_FMT)
TAG_SZ     = 16

def stream_decrypt(s3_body, magic: bytes, key: bytes, nonce: bytes) -> Iterator[bytes]:
    """
    AES-256-GCM 流式解密；tag 在末尾 16B。
    log: 解密后再 zstd 流式解压；dump: 直接输出。
    我们用 cryptography 的低级 API + 16B 尾窗口实现真流。
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    # 占位 tag (会在 finalize_with_tag 时替换); 这里走 update + finalize_with_tag 路线
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag=None, min_tag_length=TAG_SZ),
                    backend=default_backend())
    dec = cipher.decryptor()
    zdec = zstandard.ZstdDecompressor().decompressobj() if magic == b"SDKL" else None

    tail = b""
    CHUNK = 1024 * 1024
    while True:
        buf = s3_body.read(CHUNK)
        if not buf:
            break
        data = tail + buf
        if len(data) <= TAG_SZ:
            tail = data
            continue
        body, tail = data[:-TAG_SZ], data[-TAG_SZ:]
        plain = dec.update(body)
        if plain:
            yield zdec.decompress(plain) if zdec else plain

    # 最后 tail 即 tag
    if len(tail) != TAG_SZ:
        raise HTTPException(500, "ciphertext truncated")
    dec.finalize_with_tag(tail)
    if zdec:
        rest = zdec.flush()
        if rest:
            yield rest


def parse_header(blob: bytes):
    if len(blob) < HEADER_SZ:
        raise HTTPException(500, "header short")
    magic, ver, key_id, nonce = struct.unpack(HEADER_FMT, blob[:HEADER_SZ])
    if magic not in (b"SDKL", b"SDKD") or ver != 1:
        raise HTTPException(500, "bad magic/version")
    kid = key_id.rstrip(b"\x00").decode()
    if kid not in KEYRING:
        raise HTTPException(500, f"unknown key id {kid}")
    return magic, kid, nonce


# ---------- 路由：登录 / 列表 -------------------------------------------
@app.get("/login")
async def login(req: Request):
    return await oauth.sso.authorize_redirect(req, str(req.url_for("auth_cb")))

@app.get("/auth/cb", name="auth_cb")
async def auth_cb(req: Request):
    token = await oauth.sso.authorize_access_token(req)
    info  = token.get("userinfo") or await oauth.sso.userinfo(token=token)
    req.session["user"] = dict(info)
    return RedirectResponse("/")

@app.get("/logout")
def logout(req: Request):
    req.session.clear()
    return RedirectResponse("/")

@app.get("/", response_class=HTMLResponse)
def index(req: Request, user=Depends(current_user)):
    return templates.TemplateResponse("search.html", {"request": req, "user": user})

@app.get("/api/search")
def search(
    user=Depends(current_user),
    kind: str = Query("dump", pattern="^(log|dump)$"),
    product: str | None = None, channel: str | None = None,
    release: str | None = None, device: str | None = None,
    crash_signature: str | None = None,
    start: int | None = None, end: int | None = None,
    cursor: int = 0, limit: int = 50,
):
    sql = ["SELECT id, kind, product, channel, app_release, platform, device_id, "
           "occurred_at, size, sha256, crash_signature, state, uploaded_at "
           "FROM artifacts WHERE kind=:kind AND state='uploaded' AND deleted_at IS NULL"]
    p: dict = {"kind": kind}
    if product:         sql.append("AND product=:product");           p["product"]=product
    if channel:         sql.append("AND channel=:channel");           p["channel"]=channel
    if release:         sql.append("AND app_release=:release");       p["release"]=release
    if device:          sql.append("AND device_id=:device");          p["device"]=device
    if crash_signature: sql.append("AND crash_signature=:sig");       p["sig"]=crash_signature
    if start:           sql.append("AND occurred_at>=to_timestamp(:start)"); p["start"]=start
    if end:             sql.append("AND occurred_at<=to_timestamp(:end)");   p["end"]=end
    if cursor:          sql.append("AND id<:cursor");                 p["cursor"]=cursor
    sql.append("ORDER BY id DESC LIMIT :limit"); p["limit"]=min(limit, 200)

    with engine.connect() as cx:
        rows = cx.execute(text(" ".join(sql)), p).mappings().all()
    return {"items": list(rows), "next_cursor": (rows[-1]["id"] if rows else None)}


# ---------- 路由：下载 --------------------------------------------------
def _load_art(id: int):
    with engine.connect() as cx:
        row = cx.execute(text("SELECT * FROM artifacts WHERE id=:id"), {"id": id}).one_or_none()
    if not row:
        raise HTTPException(404, "not found")
    return row

@app.get("/d/raw/{id}")
def download_raw(id: int, user=Depends(current_user)):
    art = _load_art(id)
    require_can_read(user, art.channel)
    audit(user, "download_raw", art)
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": art.bucket, "Key": art.object_key,
                "ResponseContentDisposition": f'attachment; filename="{id}.enc"'},
        ExpiresIn=120,
    )
    return RedirectResponse(url, status_code=302)

@app.get("/d/plain/{id}")
def download_plain(id: int, user=Depends(current_user)):
    art = _load_art(id)
    require_can_read(user, art.channel)
    audit(user, "download_plain", art)

    body   = s3.get_object(Bucket=art.bucket, Key=art.object_key)["Body"]
    header = body.read(HEADER_SZ)
    magic, kid, nonce = parse_header(header)
    key    = KEYRING[kid]

    fn = f"{art.product}-{art.app_release}-{id}.{ 'log' if art.kind=='log' else 'dmp' }"
    return StreamingResponse(
        stream_decrypt(body, magic, key, nonce),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{fn}"'},
    )

@app.get("/d/preview/{id}")
def preview(id: int, head: int = Query(S.PREVIEW_MAX, le=S.PREVIEW_MAX), user=Depends(current_user)):
    art = _load_art(id)
    if art.kind != "log":
        raise HTTPException(400, "preview only for log")
    require_can_read(user, art.channel)
    audit(user, "preview", art, head=head)

    body   = s3.get_object(Bucket=art.bucket, Key=art.object_key)["Body"]
    header = body.read(HEADER_SZ)
    magic, kid, nonce = parse_header(header)
    out = io.BytesIO()
    for chunk in stream_decrypt(body, magic, KEYRING[kid], nonce):
        out.write(chunk)
        if out.tell() >= head:
            break
    return Response(content=out.getvalue()[:head], media_type="text/plain; charset=utf-8")


# ---------- 路由：符号化 ------------------------------------------------
@app.post("/api/symbolicate/{id}")
def symbolicate(id: int, user=Depends(current_user)):
    art = _load_art(id)
    if art.kind != "dump":
        raise HTTPException(400, "dump only")
    if not S.SENTRY_DSN:
        raise HTTPException(501, "sentry minidump endpoint not configured")
    require_can_read(user, art.channel)
    audit(user, "symbolicate", art)

    # 1) 解密到内存 (小型 dump 通常 <= 几十 MB)
    body   = s3.get_object(Bucket=art.bucket, Key=art.object_key)["Body"]
    header = body.read(HEADER_SZ)
    magic, kid, nonce = parse_header(header)
    buf = b"".join(stream_decrypt(body, magic, KEYRING[kid], nonce))

    # 2) POST 给 Sentry
    files = {"upload_file_minidump": ("crash.dmp", buf, "application/octet-stream")}
    data  = {"sentry[release]": art.app_release,
             "sentry[tags][device_id]": art.device_id,
             "sentry[tags][channel]": art.channel}
    r = httpx.post(S.SENTRY_DSN, data=data, files=files, timeout=60)
    return {"sentry_status": r.status_code, "sentry_body": r.text[:2000]}


@app.get("/audit")
def view_audit(req: Request, user=Depends(current_user), tail: int = 500):
    require_admin(user)
    try:
        with open(S.AUDIT_LOG, "rb") as f:
            f.seek(0, 2)
            sz = f.tell()
            f.seek(max(0, sz - 200 * tail))
            data = f.read().decode("utf-8", errors="replace").splitlines()[-tail:]
    except FileNotFoundError:
        data = []
    return {"lines": [json.loads(x) for x in data if x.strip()]}


@app.get("/healthz")
def healthz():
    return {"ok": True, "keys": list(KEYRING.keys()), "ts": int(time.time())}