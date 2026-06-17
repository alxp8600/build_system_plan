""" upload-token — 生成预签名上传 URL, 客户端凭 URL 直传 MinIO

key 格式: {data_type}/{platform}/{version}/{date}/{uid}/{subdir}/{uuid}.{ext}
  data_type: cdc | cds
  platform:  windows | mac | linux | android | ios
  version:   1.0.0
  date:      2026-06-15 (精度到天)
  uid:       设备/用户唯一标识
  subdir:    log | dump
"""
import os, uuid
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
import psycopg
import boto3

app = FastAPI(title="upload-token", version="0.2.0")

# ---------- config from env ----------
S3_ENDPOINT   = os.environ["S3_ENDPOINT"]
S3_PUBLIC     = os.environ["S3_PUBLIC_ENDPOINT"]
S3_AK = os.environ["S3_AK"]
S3_SK = os.environ["S3_SK"]
DB_URL = os.environ["DB_URL"]

s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT,
                  aws_access_key_id=S3_AK, aws_secret_access_key=S3_SK)

VALID_DATA_TYPES = {"cdc", "cds"}
VALID_PLATFORMS  = {"windows", "mac", "linux", "android", "ios"}
VALID_KINDS      = {"log", "dump"}

# ---------- routes ----------
@app.post("/v1/presign")
def presign(kind: str = "log", extension: str = "zip",
            data_type: str = "cdc",
            platform: str = "linux",
            version: str = "0.1.0",
            uid: str = "demo-device"):
    """直接生成 MinIO presigned PUT URL，无鉴权."""
    if kind not in VALID_KINDS:
        raise HTTPException(400, f"kind must be one of {VALID_KINDS}")
    if data_type not in VALID_DATA_TYPES:
        raise HTTPException(400, f"data_type must be one of {VALID_DATA_TYPES}")
    if platform not in VALID_PLATFORMS:
        raise HTTPException(400, f"platform must be one of {VALID_PLATFORMS}")

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"{data_type}/{platform}/{version}/{date_str}/{uid}/{kind}/{uuid.uuid4().hex}.{extension}"
    bucket = "sdk-logs"  # 统一使用 sdk-logs 桶，kind(subdir) 区分 log/dump

    url = s3.generate_presigned_url("put_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=3600)
    with psycopg.connect(DB_URL) as conn:
        conn.execute("""INSERT INTO artifacts (kind, data_type, platform, app_release, uid, occurred_at, bucket, object_key, size, sha256, state)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                     (kind, data_type, platform, version, uid,
                      datetime.now(timezone.utc), bucket, key, 0, "-" * 64, "pending"))
        conn.commit()
    return {"upload_url": url, "key": key, "bucket": bucket}