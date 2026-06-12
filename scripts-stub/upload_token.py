"""
sdk-build-plan/scripts-stub/upload_token.py

upload-token-service  (FastAPI, ~150 lines)
功能：
  1. POST /v1/activate          客户端首次激活, 拿短期 device JWT
  2. POST /v1/upload            kind=log|dump 换一把 presigned PUT URL + object key
  3. POST /v1/upload/ack        客户端上传完成回调, 写 catalog DB, 打 S3 Tag

部署：
  uvicorn upload_token:app --host 0.0.0.0 --port 8000

依赖：
  fastapi uvicorn[standard] boto3 sqlalchemy psycopg[binary] python-jose[cryptography] pydantic-settings redis
"""
from __future__ import annotations

import os, time, uuid, hashlib, json, hmac
from datetime import datetime, timezone, timedelta

import boto3
from botocore.config import Config as BotoConfig
from fastapi import FastAPI, Header, HTTPException, Request
from jose import jwt
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text


# ---------- 配置 ---------------------------------------------------------
class S:
    JWT_SECRET    = os.environ["JWT_SECRET"]
    JWT_ALG       = "HS256"
    JWT_TTL_SEC   = 24 * 3600
    ACTIVATE_HMAC = os.environ["ACTIVATE_HMAC"]      # 与 SDK 内置预共享
    S3_ENDPOINT   = os.environ["S3_ENDPOINT"]        # https://s3.intra
    S3_PUBLIC_ENDPOINT = os.environ.get("S3_PUBLIC_ENDPOINT", os.environ["S3_ENDPOINT"])
    S3_REGION     = os.environ.get("S3_REGION", "us-east-1")
    S3_AK         = os.environ["S3_AK"]
    S3_SK         = os.environ["S3_SK"]
    DB_URL        = os.environ["DB_URL"]
    BUCKET_LOG    = "sdk-logs"
    BUCKET_DUMP   = "sdk-dumps"
    MAX_SIZE      = 200 * 1024 * 1024                # 200MB


# ---------- 全局 ---------------------------------------------------------
app = FastAPI(title="sdk upload-token")
engine = create_engine(S.DB_URL, pool_pre_ping=True, future=True)
s3 = boto3.client(
    "s3",
    endpoint_url=S.S3_ENDPOINT,
    aws_access_key_id=S.S3_AK,
    aws_secret_access_key=S.S3_SK,
    region_name=S.S3_REGION,
    config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
)
# 给客户端的 presigned URL 走公网 endpoint
s3_pub = boto3.client(
    "s3",
    endpoint_url=S.S3_PUBLIC_ENDPOINT,
    aws_access_key_id=S.S3_AK,
    aws_secret_access_key=S.S3_SK,
    region_name=S.S3_REGION,
    config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
)


# ---------- 模型 ---------------------------------------------------------
class ActivateReq(BaseModel):
    product: str
    channel: str = Field(pattern=r"^(dev|staging|release)$")
    app_release: str                                 # product@version+sha
    device_id: str
    platform: str
    os: str
    sig: str                                         # HMAC(activate_hmac, product|channel|device_id|ts)
    ts: int


class UploadReq(BaseModel):
    kind: str = Field(pattern=r"^(log|dump)$")
    size: int = Field(gt=0, le=S.MAX_SIZE)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    occurred_at: int                                 # epoch sec
    crash_signature: str | None = None               # 仅 dump
    meta: dict = Field(default_factory=dict)


class AckReq(BaseModel):
    artifact_id: int
    server_sha256: str | None = None                  # 可选客户端再次校验


# ---------- 工具 ---------------------------------------------------------
def sign_dev_jwt(payload: dict) -> str:
    payload = {**payload,
               "iat": int(time.time()),
               "exp": int(time.time()) + S.JWT_TTL_SEC}
    return jwt.encode(payload, S.JWT_SECRET, algorithm=S.JWT_ALG)

def require_dev_jwt(auth: str | None) -> dict:
    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer")
    try:
        return jwt.decode(auth[7:], S.JWT_SECRET, algorithms=[S.JWT_ALG])
    except Exception as e:
        raise HTTPException(401, f"bad token: {e}")

def build_object_key(kind: str, claims: dict, sha256: str) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    base = (f"{claims['channel']}/{claims['product']}/"
            f"{claims['app_release']}/{claims['platform']}/"
            f"{now.strftime('%Y/%m/%d')}/{claims['device_id']}/"
            f"{now.strftime('%H%M%S')}-{sha256[:8]}")
    if kind == "log":
        return S.BUCKET_LOG, base + ".log.zst.enc"
    return S.BUCKET_DUMP, base + ".dmp.enc"


# ---------- 路由 ---------------------------------------------------------
@app.post("/v1/activate")
def activate(r: ActivateReq):
    if abs(time.time() - r.ts) > 300:
        raise HTTPException(401, "ts skew")
    msg = f"{r.product}|{r.channel}|{r.device_id}|{r.ts}".encode()
    expect = hmac.new(S.ACTIVATE_HMAC.encode(), msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, r.sig):
        raise HTTPException(401, "bad sig")

    token = sign_dev_jwt({
        "product": r.product, "channel": r.channel,
        "app_release": r.app_release, "device_id": r.device_id,
        "platform": r.platform, "os": r.os,
    })
    return {"token": token, "expires_in": S.JWT_TTL_SEC}


@app.post("/v1/upload")
def upload(r: UploadReq, authorization: str | None = Header(None)):
    claims = require_dev_jwt(authorization)
    if r.kind == "dump" and not r.crash_signature:
        raise HTTPException(400, "crash_signature required for dump")

    bucket, key = build_object_key(r.kind, claims, r.sha256)

    presigned = s3_pub.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ContentLength": r.size,
            "ContentType": "application/octet-stream",
            "Metadata": {
                "sha256": r.sha256,
                "device": claims["device_id"],
                "release": claims["app_release"],
                "channel": claims["channel"],
            },
        },
        ExpiresIn=300,
        HttpMethod="PUT",
    )

    with engine.begin() as cx:
        aid = cx.execute(text("""
            INSERT INTO artifacts(kind, product, channel, app_release, platform, os,
                                  device_id, occurred_at, bucket, object_key,
                                  size, sha256, crash_signature, meta, state)
            VALUES (:kind,:product,:channel,:rel,:plat,:os,
                    :dev,:occ,:bk,:key,:sz,:sha,:sig,:meta,'pending')
            RETURNING id
        """), dict(
            kind=r.kind, product=claims["product"], channel=claims["channel"],
            rel=claims["app_release"], plat=claims["platform"], os=claims["os"],
            dev=claims["device_id"],
            occ=datetime.fromtimestamp(r.occurred_at, timezone.utc),
            bk=bucket, key=key, sz=r.size, sha=r.sha256,
            sig=r.crash_signature, meta=json.dumps(r.meta),
        )).scalar_one()

    return {
        "artifact_id": aid,
        "bucket": bucket, "key": key,
        "presigned_url": presigned,
        "headers": {"Content-Type": "application/octet-stream"},
        "expires_in": 300,
    }


@app.post("/v1/upload/ack")
def ack(r: AckReq, authorization: str | None = Header(None)):
    claims = require_dev_jwt(authorization)

    with engine.begin() as cx:
        row = cx.execute(text("""
            SELECT bucket, object_key, sha256, channel, product, app_release, kind
              FROM artifacts WHERE id = :id AND device_id = :dev
        """), {"id": r.artifact_id, "dev": claims["device_id"]}).first()
        if not row:
            raise HTTPException(404, "not found")

        # 1) HEAD 对象, 校验 sha
        try:
            head = s3.head_object(Bucket=row.bucket, Key=row.object_key)
        except Exception:
            raise HTTPException(409, "object not present in s3")

        cli_sha = head.get("Metadata", {}).get("sha256")
        if cli_sha and cli_sha != row.sha256:
            raise HTTPException(409, "sha mismatch")

        # 2) 打 S3 Tag, 给 lifecycle 用
        s3.put_object_tagging(
            Bucket=row.bucket, Key=row.object_key,
            Tagging={"TagSet": [
                {"Key": "channel", "Value": row.channel},
                {"Key": "product", "Value": row.product},
                {"Key": "release", "Value": row.app_release},
                {"Key": "kind",    "Value": row.kind},
            ]},
        )

        cx.execute(text("UPDATE artifacts SET state='uploaded', uploaded_at=now() WHERE id=:id"),
                   {"id": r.artifact_id})

    return {"ok": True}


@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": int(time.time())}