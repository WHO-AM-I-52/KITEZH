# download_wpy.ps1 - skachivanie posledney portativnoy versii WPy
# Ne ispolzuet GitHub API (izbezhit 403)
param([string]$TargetDir)

if (-not $TargetDir) {
    Write-Host "[OSHIBKA] Ne ukazan TargetDir"
    exit 1
}

try {
    Write-Host "  Ishchu poslednyuyu versiyu na winpython.github.io..."

    $wc = New-Object System.Net.WebClient
    $wc.Headers.Add('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)')

    $html = $wc.DownloadString('https://winpython.github.io/')

    # Ishchem ssylku vida: .../releases/download/.../Winpython64-X.X.X.X.dot.exe
    $pattern = 'https://github\.com/winpython/winpython/releases/download/[^"''\s]+Winpython64[^"''\s]+dot[^"''\s]+\.exe'
    $match = [regex]::Match($html, $pattern)

    if (-not $match.Success) {
        # Fallback - probuy releases/latest redirect
        Write-Host "  Probuy releases/latest..."
        $html2 = $wc.DownloadString('https://github.com/winpython/winpython/releases/latest')
        $match = [regex]::Match($html2, $pattern)
    }

    if (-not $match.Success) {
        Write-Host "[OSHIBKA] Ne udalos nayti ssylku na WPy."
        Write-Host "Skachain vruchnuyu s: https://winpython.github.io/"
        exit 1
    }

    $url = $match.Value
    $fileName = Split-Path $url -Leaf
    Write-Host ("  Nayden: " + $fileName)

    $outFile = Join-Path $TargetDir "wpy_setup.exe"
    Write-Host "  Skachivanie (3-7 minut)..."
    $wc.DownloadFile($url, $outFile)
    Write-Host "  OK: skachano!"

    $fileName | Out-File (Join-Path $TargetDir ".wpy_name.txt") -Encoding utf8
    exit 0

} catch {
    Write-Host ("[OSHIBKA] " + $_.Exception.Message)
    exit 1
}
