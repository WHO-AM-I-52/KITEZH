# download_wpy.ps1 — skachivает poslednuyu portativnuyu versiyu WPy
# Parametr: papka kuda raspakovat'
param([string]$TargetDir)

if (-not $TargetDir) {
    Write-Host "[OSHIBKA] Ne ukazan TargetDir"
    exit 1
}

try {
    Write-Host "  Zapros GitHub API..."
    $releases = Invoke-RestMethod 'https://api.github.com/repos/winpython/winpython/releases' -UseBasicParsing
    $asset = $null
    foreach ($rel in $releases) {
        foreach ($a in $rel.assets) {
            if ($a.name -match 'Winpython64.*dot\.exe$') {
                $asset = $a
                break
            }
        }
        if ($asset) { break }
    }
    if (-not $asset) {
        Write-Host "[OSHIBKA] Reliz WPy ne nayden na GitHub."
        exit 1
    }
    Write-Host ("  Nayden: " + $asset.name)
    Write-Host ("  Razmer: " + [math]::Round($asset.size/1MB,1) + " MB")
    $outFile = Join-Path $TargetDir "wpy_setup.exe"
    Write-Host "  Skachivanie..."
    $wc = New-Object System.Net.WebClient
    $wc.DownloadFile($asset.browser_download_url, $outFile)
    Write-Host "  OK: skachano -> $outFile"
    $asset.name | Out-File (Join-Path $TargetDir ".wpy_name.txt") -Encoding utf8
    exit 0
} catch {
    Write-Host ("[OSHIBKA] " + $_.Exception.Message)
    exit 1
}
