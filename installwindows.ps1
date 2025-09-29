pushd $PSScriptRoot

# Install crash dump registry
#Write-Host Installing crash dump registry
#Start-Process cmd.exe -Wait -Verb runAs -ArgumentList ("/C", "cd", $(Get-Location), "&&", "reg.exe", "IMPORT", "localdumps.reg")

# Download procdump.exe, check cdb.exe, etc
python winrunner.py -i

popd
