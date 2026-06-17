""" decrypt-proxy — Web 查询 + MinIO 明文透传下载 """
import os, json
from datetime import datetime, timezone
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
import psycopg
import boto3

app = FastAPI(title="decrypt-proxy", version="0.3.0")

DB_URL = os.environ["DB_URL"]
S3_ENDPOINT = os.environ["S3_ENDPOINT"]
S3_AK = os.environ["S3_AK"]
S3_SK = os.environ["S3_SK"]
AUDIT_LOG = os.environ.get("DECRYPT_PROXY_AUDIT_LOG", "/audit/audit.jsonl")

s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT,
                  aws_access_key_id=S3_AK, aws_secret_access_key=S3_SK)

def audit(action: str, detail: dict):
    with open(AUDIT_LOG, "a") as fa:
        json.dump({"ts": datetime.now(timezone.utc).isoformat(), "action": action, "detail": detail}, fa)
        fa.write("\n")

# ---------- search ----------
@app.get("/api/search")
def search(kind: str = "log", data_type: str = None, platform: str = None,
           app_release: str = None, uid: str = None, crash_signature: str = None,
           page: int = 1, size: int = 20):
    conditions = ["deleted_at IS NULL", "kind = %s"]
    params = [kind]
    if data_type: conditions.append("data_type = %s"); params.append(data_type)
    if platform: conditions.append("platform = %s"); params.append(platform)
    if app_release: conditions.append("app_release = %s"); params.append(app_release)
    if uid: conditions.append("uid = %s"); params.append(uid)
    if crash_signature: conditions.append("crash_signature = %s"); params.append(crash_signature)
    where = " AND ".join(conditions)
    offset = (page - 1) * size
    with psycopg.connect(DB_URL) as conn:
        cur = conn.execute(f"SELECT COUNT(*) FROM artifacts WHERE {where}", params)
        total = cur.fetchone()[0]
        cur = conn.execute(f"SELECT kind, data_type, platform, app_release, uid, occurred_at, bucket, object_key, size, state FROM artifacts WHERE {where} ORDER BY occurred_at DESC LIMIT %s OFFSET %s", params + [size, offset])
        rows = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
    audit("search", {"kind": kind, "results": len(rows)})
    return {"total": total, "page": page, "size": size, "items": rows}

# ---------- 崩溃聚合 ----------
@app.get("/api/dump-groups")
def dump_groups(data_type: str = None, platform: str = None):
    conditions = []
    params = []
    if data_type: conditions.append("data_type = %s"); params.append(data_type)
    if platform: conditions.append("platform = %s"); params.append(platform)
    where = " AND ".join(conditions) if conditions else "1=1"
    with psycopg.connect(DB_URL) as conn:
        cur = conn.execute(f"SELECT crash_signature, platform, data_type, occurrences, last_seen, first_seen, uids, releases, release_list FROM dump_groups WHERE {where} ORDER BY occurrences DESC", params)
        rows = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
    audit("dump-groups", {"filters": {"data_type": data_type, "platform": platform}, "results": len(rows)})
    return {"total": len(rows), "items": rows}

# ---------- MinIO 明文透传下载 ----------
@app.get("/api/download")
def download(bucket: str, key: str):
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read()
    content_type = obj.get("ContentType", "application/octet-stream")
    audit("download", {"bucket": bucket, "key": key, "size": len(body)})
    with psycopg.connect(DB_URL) as conn:
        conn.execute("UPDATE artifacts SET state = 'uploaded', uploaded_at = now() WHERE bucket = %s AND object_key = %s", (bucket, key))
        conn.commit()
    filename = key.split("/")[-1] if "/" in key else key
    return StreamingResponse(
        iter([body]),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )