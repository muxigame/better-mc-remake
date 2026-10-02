# Platform skin source migration

The published 1.4.25 pack includes CustomSkinLoader_ForgeV3-14.24.jar as Managed,
but its configuration is Seed. SyncEngine preserves an existing config on its
initial revision baseline, so a previous local/global source can survive.

The patch uses the existing JSON overlay engine to enforce only loadlist and
enableLocalProfileCache. The source is the existing platform CustomSkinAPI with
the player's numeric UID in the request path. Other preferences stay unchanged.

PCL's custom offline skin feature is a separate mechanism. Its official source
creates resourcepacks/PCL2 Skin.zip and replaces all nine default player texture
paths with one picture for Minecraft 1.19.3+. This explains why several default
players can appear as the same locally selected skin. The options overlay only
removes the exact resourcePacks entries file/PCL2 Skin.zip and PCL2 Skin.zip. The
zip file and unrelated packs are kept. The inspected packaged resourcepacks do
not contain that default-player replacement.

Primary source:
https://github.com/Hex-Dragon/PCL2/blob/main/Plain%20Craft%20Launcher%202/Modules/Minecraft/ModLaunch.vb#L1822

Run `python client/tests/run_skin_overlay_smoke.py` to test the actual manifest
and ConfigOverlay JSON/list implementation without NuGet credentials or launching
the application. It covers stale/missing config, unrelated preference retention,
idempotence, and exact PCL entry removal.

The upload route /api/v1/player/skin stores by authenticated account UID; public
CSL lookup reads the same UID from the same SkinStore. Both use the existing game
API. The configured production container persists the database and skin directory
together in server-data. No upload request, account change or credential read is
part of this investigation.

A separate pre-existing issue was found: replacing/resetting a skin immediately
deletes an unused old hashed PNG despite immutable texture URLs and cached profile
JSON. A stale client can get 404 on that old URL. It was not observed for the four
authorized current samples and is outside this client profile-isolation patch.
It should receive a separate bounded-retention regression test/change rather than
being called the proven cause of the reported flash.
