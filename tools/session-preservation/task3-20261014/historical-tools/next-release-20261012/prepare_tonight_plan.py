from pathlib import Path
import json
root=Path(__file__).resolve().parent
p=root/'release-plan.json';plan=json.loads(p.read_text(encoding='utf-8'))
plan.update(status='TESTING_AWAITING_FINAL_SCOPE_AND_RELEASE_WINDOW',mcStarted=True,productionStop=False,manifestSwitched=False,newBackupCreated=False)
plan['tonightInstruction']='2026-10-12 09:38 UTC: prepare tonight release; no new backup; no actual stop yet; next MC window priority is real music upload-burn-play.'
plan['productionSequence']=[
 'Freeze exact version and subset after fullpack receipts and music owner handoff; keep failed/unapproved candidates out.',
 'Re-read live manifest bytes/hash and production JVM/mod hashes; stop on drift from the recorded 1.4.34 baseline.',
 'Check existing rollback materials and free space. No new world/server backup under current instruction.',
 'Build client delta from live manifest, keeping unchanged Managed/Seed policies; same-name Core must receive new hash; remove superseded renamed JAR rows. Preserve six new YSM Managed target paths and all six original race rider IDs/bytes.',
 'Stage immutable content-addressed client objects and server candidate files; verify remote bytes and dependencies before touching live selection. Do not switch manifest yet.',
 'At coordinated window use normal save-all flush then normal stop; wait for actual JVM exit and exclusive world lock. Never force-kill production or user game.',
 'Install frozen server products using existing authorized release path; if music is included deploy its verified backend/provider configuration in the agreed dependency order. No network security changes.',
 'Start production normally; verify Done, actual ticks, expected mod hashes, server status/players handshake, logs, dimensions and critical features; failures use existing rollback material and keep old public manifest.',
 'Only after server health pass upload final client manifest as the authoritative version switch. Verify public/no-cache version, raw manifest SHA256, sampled object SHA256 and launcher Managed replacement/removal behavior.',
 'Verify client upgrade from current seed preserves unrelated user files, forces new same-name Core bytes and YSM Managed paths, and retains old race rider compatibility. Report actual production/public versions and limitations.'
]
plan['musicIntake']={
 'state':'PENDING_REAL_OSS_BURN_PLAY_AND_FORMAL_CONFIG',
 'owner':'01a0fb91-0fa5-76d9-96bd-fb5f2cae9024',
 'sourceDocument':'C:/Users/ranzh/workspace/dev/muxigame/muxi-auth/docs/music-upload-candidate.md',
 'extraComponents':[
  'Owner-final music client/server compatibility JAR(s), exact SHA256 and installation sides; do not replace frozen shop Terminal with an unmerged music-only build.',
  'muxi-auth app.main route integration plus music_audio.py, music_receipt.py, music_runtime.py, music_upload.py, music_upload_identity.py and music_workload_identity.py; use exact owner-final build/commit.',
  'Bounded OSS SDK/provider injected through music_runtime.configure_storage; existing backend OIDC signer injected in process, never copied to client/QA files; formal service environment and startup verified by owner.',
  'Verified FFmpeg executable and runtime dependencies, decoder/transcoder limits, staging cleanup/lifecycle and existing quota database behavior.',
  'Dedicated music bucket/role/OIDC provider already reported created; formal object namespace and public playback permission remain separately gated. QA two-audio/four-object authorization is not production media/* approval.',
  'Core TerminalPassportApi.request/freshAccessToken compatibility checked against frozen Core; retain existing background music continuity and 37 frozen Terminal music entries.'
 ],
 'requiredEvidence':[
  'Real authorized account -> OSS upload -> full backend decode -> signed receipt -> original menu/CD one-time burn -> real playback, with bounded redacted object/hash evidence.',
  'Cancel, failed upload, stale/closed menu, reconnect and normal stop behavior; formal cloud read policy and provider startup verified.',
  'Exact production install/config delta and rollback instructions excluding secrets; explicit approval for any unapproved permanent public-read/security change.'
 ],
 'mustNotDo':['Treat temporary test-public objects as production permission','Upload credentials or copy backend signing keys','Use passing local/mock tests as real OSS/MC proof','Merge unvalidated music artifact into final client manifest']
}
p.write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(p)
