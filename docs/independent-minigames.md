# Independent minigames integration

The source-only repository split is separate from the running 1.4.28 release. No deployment is performed by this change.

| Role | Repository | Jar / mod ID |
| --- | --- | --- |
| Shared lifecycle / trust / terminal protocol | muxigame/muxi-minigames | muxi-minigames / muxi_minigames |
| Horse racing and trading | muxigame/muxi-horse-racing | muxi-horse-racing / muxi_horse_racing |
| Flight battle and sorties | muxigame/muxi-flight | muxi-flight / muxi_flight |
| Zombie challenge | muxigame/muxi-zombie-challenge | existing independent module |
| Outbreak | muxigame/muxi-outbreak | existing independent module |

Build framework >=0.2.0 first, then the two independent games with `--framework-jar` or the sibling default. Install all three matching jars on both client and server. The original Java package, room IDs, persistent return keys, dimension keys and resource namespace remain unchanged. `pack/verify-server-client-sync.ps1` checks the new jars alongside the framework. Never install independent game jars with the previous framework that embeds the same classes and resources.

Game source and game-specific tests/docs now belong to their game repositories. Historical full-pack acceptance does not constitute runtime acceptance of this split. A future release must run a coordinated client/server load and registration check, return/save compatibility and relevant gameplay checks before deploying.
