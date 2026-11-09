<#
.SYNOPSIS
  site-scraper for Windows (no make needed). Runs everything in Docker.

.EXAMPLE
  .\scrape.ps1 https://example.co.za
  .\scrape.ps1 https://example.co.za --max-pages 100 --insecure
  .\scrape.ps1 test
  .\scrape.ps1 build
#>
# No $ErrorActionPreference = 'Stop': Windows PowerShell 5.1 would treat Docker's progress
# messages on stderr as errors. The exit code below is Docker's.
$compose = Join-Path $PSScriptRoot 'compose.yaml'

if ($args.Count -eq 0 -or $args[0] -in @('-h', '--help', 'help')) {
    Get-Help $MyInvocation.MyCommand.Path -Detailed
    docker compose -f $compose run --rm scrape --help
    exit 0
}

switch ($args[0]) {
    'build' { docker compose -f $compose build; break }
    'test' { docker compose -f $compose run --rm test; break }
    'lint' { docker compose -f $compose run --rm test sh -c 'ruff check . && ruff format --check .'; break }
    default { docker compose -f $compose run --rm scrape @args }
}
exit $LASTEXITCODE
