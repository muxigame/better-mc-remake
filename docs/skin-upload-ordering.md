# Launcher skin upload ordering

Public launcher metadata currently identifies version 1.2.3 and installer SHA256
9c8b067656045cdde28d6822d0cd4e0de8c12fde2d127e0650fed92a3bd2e9bb, matching the local
release metadata. Tauri packages client/sidecar/web as frontendDist. The inspected
upload source path is:

onSkinFile -> decode/validate PNG -> skinCall("skinSave", {png, model}) -> RpcHost
SkinRequestAsync/SendSkinAsync -> PUT https://mc.muxigame.com/api/v1/player/skin.
The sidecar attaches the active account session. WebsiteAccount.from_userinfo
takes muxi_uid, and the server uses account.uid, not a UID/name supplied in JSON.
SkinStore normalizes PNG, writes its hash texture, updates player_skins in SQLite,
commits, and returns the stored result. The UI previews that returned result.
HTTP errors reject the save and display an error toast; a file-read exception was
previously outside that error path.

Three ordering regressions were reproduced against the real app.js before the
fix: an earlier default GET overwrote a successful save in the UI, an old account's
GET appeared under a new account, and a file selected before switching accounts
was sent as a save after the switch. The last case could actually write a selected
image under the newly authenticated UID; it is not a server authorization bypass.

The fix invalidates stale reads when a write/account change starts, scopes results
to the account generation, blocks duplicate writes/new reads while saving, and
discards a selection if the account changed while reading/decoding it. File-read
failure now produces an error instead of an unhandled rejection. It does not
change authentication, UID/UUID, skin API semantics, or account data.

Validation: node --test client/tests/skin-upload.test.cjs plus the existing
pack-install/client-update tests (31 passed). The upload suite covers eight cases.
Server tests.test_skins (16 passed) includes synthetic UID 90001/90002 uploads,
two different PNGs, reopening the same store to check committed persistence,
public profile/texture matching, and ignoring a different UID supplied in JSON.
All authentication/storage in these API tests are local mocks/temporary files.

No write/persistence failure was reproduced for the normal synthetic API path.
The four authorized real public profiles returning Steve therefore remain a
historical-data discrepancy, not proof that the user misremembered setting skins.
The inspected launcher logs do not contain skin-save request outcomes. The next
evidence should be sanitized server route/status logs for /api/v1/player/skin and
the relevant UID/hash/model skin records, without accessing sessions or tokens.
No real-account upload/reset was performed. The old-hash deletion issue is recorded
separately in skin-source-migration.md and is not changed by this patch.

The patch is separate from the earlier Core/profile-isolation and source/PCL
configuration patches. Only the designated operations worker should integrate,
build and deploy it. No GUI validation, deployment or server restart was performed
here. The 131 GUI slot still requires its existing worker to release it and the
user's interaction precheck.
