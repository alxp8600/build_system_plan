# Check what's in MinIO vs what's in DB
$mc = "docker"
$mcArgs = @("exec", "sdk-build-demo-minio-1", "mc", "ls", "--recursive", "local/sdk-logs/")
Write-Host "=== MinIO sdk-logs bucket contents (first 5) ==="
& $mc $mcArgs 2>&1 | Select-Object -First 5

Write-Host ""
Write-Host "=== DB artifacts with state=uploaded ==="
docker exec sdk-build-demo-catalog-db-1 psql -U catalog -d catalog -t -c "SELECT kind, state, size, bucket, substring(object_key,1,60) FROM artifacts LIMIT 10;" 2>&1