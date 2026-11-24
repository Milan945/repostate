# Install the repostate skills on Windows.
#   .\install.ps1                  # Claude Code, all projects (~\.claude\skills)
#   .\install.ps1 -Project         # Claude Code, this project only (.\.claude\skills)
#   .\install.ps1 -Dir C:\path     # any other skills folder
param([switch]$Project, [string]$Dir)
$ErrorActionPreference = "Stop"

$target = Join-Path $HOME ".claude\skills"
if ($Project) { $target = Join-Path (Get-Location) ".claude\skills" }
if ($Dir) { $target = $Dir }

$src = Join-Path $PSScriptRoot "skills"
if (-not (Test-Path (Join-Path $src "repostate\SKILL.md"))) {
  $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("repostate-" + [guid]::NewGuid())
  git clone --depth 1 --quiet https://github.com/Milan945/repostate $tmp
  $src = Join-Path $tmp "skills"
}

New-Item -ItemType Directory -Force -Path $target | Out-Null
foreach ($skill in "repostate", "repostate-review", "repostate-architecture") {
  $dest = Join-Path $target $skill
  if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
  Copy-Item -Recurse (Join-Path $src $skill) $dest
  Write-Host "installed $skill -> $dest"
}
Write-Host ""
Write-Host "Done. In any repo, ask your agent to `"set up repostate`", or run:"
Write-Host "  python `"$target\repostate\scripts\repostate.py`" init"
