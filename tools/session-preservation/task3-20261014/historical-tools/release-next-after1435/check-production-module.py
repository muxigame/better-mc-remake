from pathlib import Path
import json
from remote import remote
N=Path(__file__).resolve().parent
code=r"""$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$s='C:\Users\Administrator\WorkSpace\muxigame\bmc5server';$lines=Get-Content "$s\logs\latest.log"; $load=@($lines|Where-Object{$_ -match 'muxi Outbreak loaded|Loaded 1 outbreak maps: \[lostschool\]|Done \('});$fatal=@($lines|Where-Object{$_ -match 'Failed to start the minecraft server|Exception in server tick loop|Failed to load mod|OutOfMemoryError'});if(-not($load|Where-Object{$_ -match 'Loaded 1 outbreak maps: \[lostschool\]'})){throw 'Outbreak map startup not confirmed'};if($fatal.Count){throw 'Fatal startup log found'};@{atUtc=[DateTime]::UtcNow.ToString('o');loadEvidence=@($load|ForEach-Object{[string]$_}|ForEach-Object{$_.Substring(0,[Math]::Min(400,$_.Length))});fatalStartupPatterns=$fatal.Count}|ConvertTo-Json -Depth 5"""
r=json.loads(remote(code));(N/'production-module-load.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(r,ensure_ascii=False))
