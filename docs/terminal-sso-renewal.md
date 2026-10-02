# 131 terminal SSO: accepted dev handoff, 2026-10-02

The requested three-round native lifecycle suite passed against isolated actual
Auth/Web HTTPS services, the actual RpcHost renewal broker and DPAPI storage,
three fresh Minecraft/CEF processes, and actual dedicated-server Core listeners.
This is local developer acceptance. Production SSO remains disabled; no release,
push, tag, production configuration or secret distribution occurred.

## Result and scope

The rounds exercised A-to-B switch (10000), fresh B process recovery (10001),
and B source revocation (10001). All game exits were zero. Each round verified
source-access expiry and refresh rotation, website-cookie expiry and silent
native re-exchange, actual Esc close/reopen, a real disconnect while an issuer
ticket was in flight, and fresh proof on the new listener. Switching/revoking
disarmed the old view and retained-cookie requests. The final logout cleared
the actual DPAPI session. No interactive browser authorization request occurred.

There were 12 native exchange POSTs and 12 legal Result packets bound to the
actual online player/listener, plus six login and six disconnect-revocation
events. Ordinary player permissions and reversible skin changes passed; both
synthetic test accounts were restored. No identity/ticket/callback fixture mixin
was active. Initial authorization used synthetic isolated QA accounts; this is
not a production OAuth-login acceptance or a blanket claim for other features.

Machine-readable results, exact product source/artifact hashes and offline
check counts: `terminal-sso-final-acceptance-20261002.json`. Raw local evidence:
`build/sso-mcef-20261002-194425-465213-e3ca058b/`. The requested suite passed before
this documentation-only closeout; no product/test code changed afterward.

## Implementation and credential boundary

Only the launcher holds account access/refresh tokens. The game receives an
ephemeral same-user/elevation named-pipe capability, not an OAuth token. The
broker mints fresh short credentials and rejects changed account generations
before and after issuance. Late account/profile/refresh responses cannot replace
the newly selected account; successful refresh rotation is persisted with DPAPI.
Logout, account switch and game exit revoke tracked short credentials. A bounded
20-second retirement grace permits already-issued proof to finish during an
overlapping heartbeat/opening; source switch/logout revoke immediately.

Core retains PKCE/request/current-connection checks and ignores late old-listener
results. Terminal native POSTs stay bound to the browser, view, main frame and
connection. Each account child has its own CEF cookie context and no general
native bridge. Esc or screen replacement disarms authority; reopening proves it
again. Expired cookies renew once natively, without rendering a second login.
Source validation runs every 30 seconds; terminal blocking is not global
immediate revocation of ordinary website cookies. Stale prior account load/error
callbacks cannot fail a new entry exchange.

## Verification and reusable commands

- Core build/self-tests: 6942; launcher build: zero warnings/errors. Terminal
  build: success, two existing icon deprecation warnings.
- C# to Java Windows broker: 9 assertions. Actual RpcHost lifecycle unit tests:
  13. Actual native Java pipe/TLS issuer probe: 17, without starting MC/CEF.
- Current navigation source: 70 + 42 component lifecycle assertions. Explicit
  deterministic fixtures in that standalone test are separate from native QA.
- TLS service-chain tests: 59. Real native suite: all three rounds above passed.
- `tests/run_terminal_navigation.py <JDK_HOME> --three-rounds`.
- `dotnet run --project tests/terminal-launcher-renewal/LauncherRenewalTests.csproj`.
- `tests/run_native_launcher_probe.py` and `tests/run_terminal_mcef.py --help`
  document the explicit runtime/tool/artifact arguments for isolated probes and
  `--suite --native` acceptance. No QA classes are packaged into product jars.

QA uses loopback HTTPS, exact temporary certificate checks and private JVM
trust. It maps only canonical issuer endpoints. The QA frontend redirects use
the canonical authorization origin while internal service calls stay loopback.
Native mode omits identity/ticket/callback fixture mixins. Private MCEF shutdown
isolation preserves normal dispose and removes only the global helper-kill
mixin. Windows Jobs cover only owned roots/descendants; all owned processes
exited, and protected PID 20496 was untouched. No OS DNS/trust was changed.

## Release-owner prerequisites, not executed

1. Review the ordinary dev commits and integrate into the explicitly approved
   release target. Current authorization permits dev commits only. Existing
   Auth/Web CI release ancestry requires master; merge/push/tag/deployment needs
   separate release authorization. Do not represent another owner's uncommitted
   changes as included in these SSO commits.
2. Securely install the dedicated terminal-SSO authority on Auth and matching
   game-server Core using the user's approved operation. HK's independent social
   and SSO keys have no distribution receipt yet. Never substitute social,
   profile or OP keys, transfer secrets between machines as an agent, or put
   secret values in chat/logs. Obtain a non-secret installation/match receipt.
3. Set Auth `MUXI_TERMINAL_SSO_ENABLED=1` and its dedicated
   `MUXI_TERMINAL_SSO_SERVER_KEY`; website `BMC_TERMINAL_SSO_ENABLED=1` with exact
   `BMC_PUBLIC_URL=https://mc.muxigame.com`, canonical account issuer and valid
   confidential better-mc-web client configuration; matching Core
   `features.terminalSso.enabled=true` and endpoint
   `https://account.muxigame.com/api/internal/minecraft/` with that SSO key.
4. Release matching launcher, client/server Core and Terminal artifacts, record
   committed source and exact release build hashes (including companion
   minigames), then verify on the actual target after authorized runtime reload.
   The accepted local jars include other owners' then-current working changes;
   their hashes identify that tested snapshot, not a clean release build.

LoginGate, identity admission and OP synchronization remain outside this rollout
and disabled. Production enablement, key distribution and release-target
integration are still pending; local acceptance does not establish production
acceptance.
