"""种子数据脚本 — sdk-logs 桶写入 40 条测试数据（20 log + 20 dump），明文存储"""
import os, uuid, hashlib, json, random
from datetime import datetime, timezone, timedelta
import boto3
import pg8000.native as pg

S3_ENDPOINT = "http://localhost:9000"
S3_AK = "admin"
S3_SK = os.environ.get("MINIO_ROOT_PASSWORD", "minio_admin_demo_2024_secret_!!_")

s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT,
                  aws_access_key_id=S3_AK, aws_secret_access_key=S3_SK)

def db():
    return pg.Connection(host="localhost", port=5432, database="catalog",
                         user="catalog", password=os.environ.get("PG_PASSWORD", "demo_pg_password_32chars_here_!"))

now = datetime.now(timezone.utc)
random.seed(42)

data_types = ["cdc", "cds"]
platforms = ["windows", "mac", "linux", "android", "ios"]
uid_list = [f"uid-{i:02d}" for i in range(1, 11)]

crash_sigs = ["SIGSEGV_0xdead", "SIGABRT_malloc", "SIGBUS_alignment",
              "SIGFPE_divzero", "SIGILL_badop"]

log_lines = [
    b"init sdk\n", b"config loaded\n", b"network OK\n", b"sync start\n",
    b"sync done\n", b"crash: null ptr\n", b"timeout 5000ms\n", b"retry 1/3\n",
    b"retry 2/3\n", b"retry success\n", b"battery low\n", b"mem warn\n",
    b"user login\n", b"enter foreground\n", b"enter background\n",
]

dump_payloads = [
    b"signal:11 registers:eax=0 ebx=1 ecx=2 stack:frame1,frame2\n",
    b"signal:6 backtrace:malloc+0x32,free+0x14\n",
    b"signal:7 addr:0xffffffff backtrace:memcpy+0x8,parse+0x21\n",
    b"signal:8 div=0 backtrace:calc+0x10,main+0x55\n",
    b"signal:4 op=0xabcdef backtrace:call+0x3,init+0x12\n",
]

total = 0

# ========== sdk-logs : 20 log entries ==========
print("=== sdk-logs (log) ===")
for i in range(20):
    data_type = random.choice(data_types)
    platform = random.choice(platforms)
    uid = random.choice(uid_list)
    version = f"{random.randint(1,3)}.{random.randint(0,9)}.{random.randint(0,9)}"
    occurred = now - timedelta(days=random.randint(0, 30), hours=random.randint(0, 23))
    content = b"".join(random.choices(log_lines, k=random.randint(2, 5)))
    sha = hashlib.sha256(content).hexdigest()
    bucket = "sdk-logs"
    date_str = occurred.strftime("%Y-%m-%d")
    key = f"{data_type}/{platform}/{version}/{date_str}/{uid}/log/{uuid.uuid4().hex}.zip"
    s3.put_object(Bucket=bucket, Key=key, Body=content)
    conn = db()
    conn.run("""INSERT INTO artifacts (kind,data_type,platform,app_release,uid,occurred_at,bucket,object_key,size,sha256,state)
                VALUES (:kind,:data_type,:platform,:version,:uid,:occurred,:bucket,:key,:size,:sha256,:state)
                ON CONFLICT (bucket, object_key) DO NOTHING""",
             kind="log", data_type=data_type, platform=platform, version=version, uid=uid,
             occurred=occurred, bucket=bucket, key=key, size=len(content), sha256=sha, state="uploaded")
    conn.close()
    print(f"  [{i+1:2d}/20] log  {data_type}/{platform}/{version} {uid}")
    total += 1

# ========== sdk-logs : 20 dump entries ==========
print("=== sdk-logs (dump) ===")
for i in range(20):
    data_type = random.choice(data_types)
    platform = random.choice(platforms)
    uid = random.choice(uid_list)
    version = f"{random.randint(1,3)}.{random.randint(0,9)}.{random.randint(0,9)}"
    occurred = now - timedelta(days=random.randint(0, 30), hours=random.randint(0, 23))
    content = random.choice(dump_payloads)
    sha = hashlib.sha256(content).hexdigest()
    csig = random.choice(crash_sigs)
    bucket = "sdk-logs"
    date_str = occurred.strftime("%Y-%m-%d")
    key = f"{data_type}/{platform}/{version}/{date_str}/{uid}/dump/{uuid.uuid4().hex}.zip"
    s3.put_object(Bucket=bucket, Key=key, Body=content)
    conn = db()
    conn.run("""INSERT INTO artifacts (kind,data_type,platform,app_release,uid,occurred_at,bucket,object_key,size,sha256,crash_signature,state)
                VALUES (:kind,:data_type,:platform,:version,:uid,:occurred,:bucket,:key,:size,:sha256,:csig,:state)
                ON CONFLICT (bucket, object_key) DO NOTHING""",
             kind="dump", data_type=data_type, platform=platform, version=version, uid=uid,
             occurred=occurred, bucket=bucket, key=key, size=len(content), sha256=sha, csig=csig, state="uploaded")
    conn.close()
    print(f"  [{i+1:2d}/20] dump {csig}  {data_type}/{platform}/{version} {uid}")
    total += 1

print(f"\nDone! 共写入 {total} 个文件（sdk-logs 桶：20 log + 20 dump）。")
print("MinIO: http://localhost:9001")
print("搜索: http://localhost:8002/api/search?kind=log&data_type=cdc")
print("崩溃聚合查询 SQL: SELECT * FROM dump_groups;")