# download_wpy.ps1 - skachivanie WPy (portativnaya .dot versiya)
# Ispolzuet pryamuyu ssylku, bez GitHub API
param([string]$TargetDir)

if (-not $TargetDir) {
    Write-Host "[OSHIBKA] Ne ukazan TargetDir"
    exit 1
}

# Versiya WPy - obnovlyay pri vyhode novoy
$WPY_VER  = "3.13.13.0"
$PY_VER   = "3.13.13"
$FNAME    = "Winpython64-$WPY_VER.dot.exe"

# Varianty URL (Github -> SourceForge kak fallback)
$URLS = @(
    "https://github.com/winpython/winpython/releases/download/$WPY_VER/$FNAME",
    "https://sourceforge.net/projects/winpython/files/WinPython_3.13/$WPY_VER/$FNAME/download"
)

$outFile = Join-Path $TargetDir $FNAME

Write-Host "  Fayl: $FNAME"
Write-Host "  Razmer: ~30 MB"

$wc = New-Object System.Net.WebClient
$wc.Headers.Add('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)')

$ok = $false
foreach ($url in $URLS) {
    try {
        Write-Host "  Skachivanie s: $url"
        Write-Host "  (mozhet zanyat 2-5 minut...)"
        $wc.DownloadFile($url, $outFile)
        if ((Get-Item $outFile).Length -gt 1MB) {
            Write-Host "  OK: skachano -> $outFile"
            $ok = $true
            break
        } else {
            Write-Host "  [WARN] Fayl slishkom malen, probuy sleduushiy URL..."
            Remove-Item $outFile -Force -ErrorAction SilentlyContinue
        }
    } catch {
        Write-Host ("  [WARN] Oshibka: " + $_.Exception.Message + " -> probuy sleduushiy URL...")
        Remove-Item $outFile -Force -ErrorAction SilentlyContinue
    }
}

if (-not $ok) {
    Write-Host "[OSHIBKA] Ne udalos skachat WPy ni s odnogo istochnika."
    Write-Host "Skachain vruchnuyu: https://github.com/winpython/winpython/releases"
    exit 1
}

$FNAME | Out-File (Join-Path $TargetDir ".wpy_name.txt") -Encoding utf8
exit 0
