<#
  Yuktra splash / loader.

  Shows a small "Starting..." window while the app boots in the background, then
  closes ITSELF automatically once:
    1. the backend /health endpoint returns 200, AND
    2. the application window appears (browser tab or webview titled
       "Equipment Intelligence" / "Yuktra").
  A hard timeout closes the splash regardless, so it can never hang forever.

  Launched by run_yuktra.ps1 as a separate hidden process.
#>
param(
    [string]$ApiBase = 'http://127.0.0.1:8009',
    [string]$TitlePattern = 'Equipment Intelligence|Yuktra',
    [int]$TimeoutSec = 300
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = 'None'
$form.StartPosition = 'CenterScreen'
$form.Size = New-Object System.Drawing.Size(460, 230)
$form.BackColor = [System.Drawing.Color]::FromArgb(20, 24, 33)
$form.TopMost = $true
$form.ShowInTaskbar = $false

$title = New-Object System.Windows.Forms.Label
$title.Text = 'Yuktra'
$title.ForeColor = [System.Drawing.Color]::White
$title.Font = New-Object System.Drawing.Font('Segoe UI', 24, [System.Drawing.FontStyle]::Bold)
$title.TextAlign = 'MiddleCenter'
$title.Dock = 'Top'
$title.Height = 80
$form.Controls.Add($title)

$status = New-Object System.Windows.Forms.Label
$status.Text = 'Starting, please wait...'
$status.ForeColor = [System.Drawing.Color]::FromArgb(170, 180, 200)
$status.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$status.TextAlign = 'MiddleCenter'
$status.Dock = 'Top'
$status.Height = 50
$form.Controls.Add($status)

$bar = New-Object System.Windows.Forms.ProgressBar
$bar.Style = 'Marquee'
$bar.MarqueeAnimationSpeed = 30
$bar.Dock = 'Bottom'
$bar.Height = 20
$form.Controls.Add($bar)

$script:startTime = [DateTime]::UtcNow
$script:backendReady = $false

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 800
$timer.Add_Tick({
        $elapsed = ([DateTime]::UtcNow - $script:startTime).TotalSeconds
        if ($elapsed -gt $TimeoutSec) {
            $timer.Stop(); $form.Close(); return
        }

        if (-not $script:backendReady) {
            try {
                $resp = Invoke-WebRequest -Uri "$ApiBase/health" -UseBasicParsing -TimeoutSec 1
                if ($resp.StatusCode -eq 200) {
                    $script:backendReady = $true
                    $status.Text = 'Backend ready. Opening application...'
                }
            }
            catch {
                $status.Text = 'Starting backend...'
            }
            return
        }

        # Backend is up; wait for the application window to appear, then close.
        $win = Get-Process |
            Where-Object { $_.MainWindowTitle -and ($_.MainWindowTitle -match $TitlePattern) } |
            Select-Object -First 1
        if ($win) {
            $timer.Stop()
            Start-Sleep -Milliseconds 500
            $form.Close()
        }
    })
$timer.Start()

[System.Windows.Forms.Application]::Run($form)
