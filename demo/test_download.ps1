$bucket = "sdk-logs"
$key = "cdc/linux/0.1.0/2026-06-15/uid-01/log/a05e24af017244e8805d4691643d59ad.zip"
$url = "http://localhost:8080/proxy/api/download?bucket=$bucket&key=$key"
Write-Host "Testing: $url"
try {
    $r = Invoke-WebRequest -Uri $url -UseBasicParsing
    Write-Host "StatusCode: $($r.StatusCode)"
    Write-Host "Content-Length: $($r.RawContentLength)"
    Write-Host "First 60 chars: $($r.Content.Substring(0, [Math]::Min(60, $r.Content.Length)))"
} catch {
    Write-Host "Error: $($_.Exception.Message)"
}