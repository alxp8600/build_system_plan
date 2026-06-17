# 同时查看数据库记录和 MinIO 实际文件
Write-Host "=== 数据库记录 ===" -ForegroundColor Cyan
docker exec sdk-build-demo-catalog-db-1 psql -U catalog -d catalog -c "SELECT object_key, state, size, bucket, kind FROM artifacts ORDER BY created_at DESC LIMIT 10;"

Write-Host ""
Write-Host "=== MinIO sdk-logs 实际文件 ===" -ForegroundColor Cyan
$listScript = @"
import boto3, json
s3 = boto3.client('s3',endpoint_url='http://minio:9000',aws_access_key_id='admin',aws_secret_access_key='minio_admin_demo_2024_secret_!!_')
resp = s3.list_objects_v2(Bucket='sdk-logs',MaxKeys=10)
items = [{'Key':o['Key'],'Size':o['Size']} for o in resp.get('Contents',[])]
print(json.dumps(items,indent=2))
"@
$listScript | docker exec -i sdk-build-demo-decrypt-proxy-1 python3 -c (Get-Content -Raw -)

Write-Host ""
Write-Host "=== MinIO sdk-packets 实际文件 ===" -ForegroundColor Cyan
$listScriptDumps = @"
import boto3, json
s3 = boto3.client('s3',endpoint_url='http://minio:9000',aws_access_key_id='admin',aws_secret_access_key='minio_admin_demo_2024_secret_!!_')
resp = s3.list_objects_v2(Bucket='sdk-packets',MaxKeys=10)
items = [{'Key':o['Key'],'Size':o['Size']} for o in resp.get('Contents',[])]
print(json.dumps(items,indent=2))
"@
$listScriptDumps | docker exec -i sdk-build-demo-decrypt-proxy-1 python3 -c (Get-Content -Raw -)