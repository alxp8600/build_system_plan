import boto3, os
s3 = boto3.client("s3", endpoint_url="http://localhost:9000",
                  aws_access_key_id="admin", aws_secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minio_admin_demo_2024_secret_!!_"))

buckets = ["sdk-logs", "sdk-packets"]
for b in buckets:
    print(f"\n=== bucket: {b} ===")
    try:
        resp = s3.list_objects_v2(Bucket=b, MaxKeys=20)
        if "Contents" in resp:
            for o in resp["Contents"]:
                print(f"  {o['Key']}  ({o['Size']} bytes)")
        else:
            print("  (empty)")
    except Exception as e:
        print(f"  ERROR: {e}")