# Query decrypt-proxy directly for a valid artifact
$searchUrl = "http://localhost:8002/api/search?kind=log&size=1&state=uploaded"
Write-Host "Searching for uploaded artifacts via decrypt-proxy..."
try {
    $r = Invoke-RestMethod -Uri $searchUrl
    if ($r.items.Count -gt 0) {
        $item = $r.items[0]
        $bucket = $item.bucket
        $key = $item.object_key
        Write-Host "Found: bucket=$bucket key=$key"
        Write-Host "Testing download..."
        
        $dlUrl = "http://localhost:8080/proxy/api/download?bucket=$bucket&key=$key"
        $r2 = Invoke-WebRequest -Uri $dlUrl -UseBasicParsing
        Write-Host "Download StatusCode: $($r2.StatusCode)"
        Write-Host "Size: $($r2.RawContentLength)"
    } else {
        Write-Host "No uploaded items found, checking all items..."
        $searchUrl2 = "http://localhost:8002/api/search?kind=log&size=3"
        $r3 = Invoke-RestMethod -Uri $searchUrl2
        foreach ($item in $r3.items) {
            Write-Host "  bucket=$($item.bucket) key=$($item.object_key) state=$($item.state) size=$($item.size)"
        }
    }
} catch {
    Write-Host "Error: $($_.Exception.Message)"
}