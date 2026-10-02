# 131 real launcher/game listener acceptance history

Current outcome: the requested actual three-round native suite PASSED. See
`terminal-sso-renewal.md` for the final handoff and
`terminal-sso-final-acceptance-20261002.json` for exact checks and hashes. All
earlier failure/pending statements below describe historical checkpoints and
are superseded by the final successful section. No production acceptance is
claimed. Product/test source was unchanged during this documentation closeout.

Status (2026-10-02): native mode is implemented and has been run on 131 against
an isolated dedicated server. The requested three-round native lifecycle acceptance now **PASSES** on the current dirty dev source; earlier failed attempts below are historical.
The first attempt exposed a QA packaging error (a dedicated-only mod entrypoint
was present in the client jar); that packaging error is fixed. The second attempt
joined the real server and recorded actual login/logout events, but the account
entry received an empty native result before any proof reached the issuer or any
CEF exchange POST occurred. Its stage-3 failure does not establish an HTTP/TLS
root cause. Do not describe it as a completed account or game-packet acceptance.

Offline actual RpcHost/DPAPI/issuer/native-pipe probes pass 16 assertions, including
ordinary Java proof requests returning HTTP 200, a delayed pipe reader, the same
selector QA agent, source-access refresh, A-to-B switch and logout. They start no
Minecraft/CEF and do not validate the complete mod-loader/game path. Native-only
read-only observers now log channel/pipe shape, issuer rewrite checks, HTTP status
and exception **class names only**, without bodies, headers or credentials. They
compile in `--native --prepare-only`, which starts no game or dedicated server.
A subsequent actual diagnostic found channel/pipe/issuer checks valid, followed
by ConnectException/ClosedChannelException before TLS. The actual game argument
`-Djava.net.preferIPv6Addresses=system` reproduces the failure in ordinary Java:
localhost selects IPv6, while Uvicorn listens only on 127.0.0.1. Native QA now
uses that exact IPv4 TLS listener. The same-argument probe passes all 16 assertions
after the fix (four HTTP 200 proofs); canonical issuer validation, certificate SAN,
private JVM trust and SPKI checks remain intact. This fixes the demonstrated QA
transport failure; full native game/CEF acceptance still requires the next window.

## Reusable QA shutdown isolation for the album owner

Use `tests/mcef_private_qa_copy.py --source <installed MCEF jar> --destination
<new jar in the owner's PRIVATE QA mods directory>`. It removes exactly the
`CefWindowsShutdownMixin` entry from MCEF's mixin JSON in the new jar. All other
jar entries and classes remain present, including normal CefApp shutdown. The
source is read only. The helper rejects the source as destination and rejects
an existing destination; it writes a hash receipt beside the private jar.

Current installed SHA256:
`0c7696216fa5cfee659d687475873c847a9a17cc8ce3a56119a69c946eeb8772`.
Current private QA SHA256:
`770062ac81b3f9bb223a25e6f1a10b0dafca9ba857f6de4a3342070bd880c55c`.

This disables the Windows workaround which first runs tasklist and then
`taskkill /F /IM jcef_helper.exe`; it is not a claim that this workaround caused
any previous run's error. Record own process PID/parent/start time and correlate
errors with other owners. Never kill by image name. A surviving owned helper
requires identity/PID/start-time verification before targeted cleanup.

## Implemented native-chain routing

1. Fixture mode cancels `TerminalPassportApi.request/validate` and supplies
   backend tickets, while other mixins replace LocalPlayer/connection during
   callbacks. Real-native mode omits these three mixins:
   `PassportRequestFixtureMixin`, `PassportIdentityFixtureMixin`, and
   `PassportCallbackFixtureMixin`. The private account CEF observer can remain;
   it only observes POST metadata and scopes the temporary test certificate.
2. `MuxiGameCore` is dedicated-server only. A real local dedicated server must
   be staged in a NEW private lab directory, using read-only existing 21.1.250
   libraries and the current Core/minigames jars. Use its own loopback port,
   synthetic world/player database, config and logs. Do not reuse installed
   server properties, whitelist/ops, auth/key files, or production worlds.
3. Enable only terminalSso with a known public synthetic test key in that lab.
   Keep LoginGate, identity and OP off. The dedicated server must execute actual
   `TerminalPassportFeature` login/logout events, retain real ServerPlayer and
   its exact online ServerGamePacketListenerImpl, and handle the actual
   Request/Result packets through TerminalPassportNetwork.
4. Retain production URL/config validation. A QA-only transport mixin should
   rewrite only the exact Auth issuer URI to the per-run loopback TLS endpoint,
   for native Core proof requests and server ticket/disconnect requests. A
   private JVM truststore contains only the generated test certificate. No
   production endpoint or system DNS/CA trust is changed.
5. Launch the actual per-game C# TerminalCredentialBroker and actual RpcHost
   mint/refresh path. The QA launcher loads only isolated synthetic launcher
   sessions and a private DPAPI account store, then uses
   TerminalCredentialEnvironment.Apply for native-only pipe/capability handoff.
   Do not provide long OAuth tokens to the game, browser, CLI or QA game control.
   A separate launcher-only control capability must gate any synthetic session
   seed endpoint; the game capability cannot retrieve launcher tokens.
6. The driver joins the private dedicated server instead of creating a single
   player world. It launches the normal terminal entry and waits for actual
   client proof -> actual packet -> actual server ticket -> native CEF POST.
   It must not manually deliver a ticket or substitute a connection/player.

## Required receipts before claiming full-chain PASS

- Real UID 10000 and 10001 clients, authentic game listeners and their offline
  profile UUID consistency; no OP or join-admission change.
- Actual native C# pipe -> current-source Core HTTP proof -> game Request/Result
  packets -> ordinary CEF website cookie; no ticket from the fixture server.
- Reversible player operations and ordinary website permissions for each UID;
  skin restored and the other UID unchanged.
- Source access expiry causes actual launcher refresh/DPAPI rotation; website
  session expiry renews silently; close/reopen and whole MC/CEF restart succeed.
- Switching A to B disables A's factory/view; late refresh/proof/packet cannot
  replace B. Actual logout/source revocation/disconnect disables the old chain.
- Redacted source/artifact hashes, PID/start times, actual auth and game-listener
  counters, screenshot/HTTP receipts; assert fixture identity/listener flags
  false rather than relabeling the current fixture suite.

Production flags, keys, LoginGate, OP trust, tags, master merges, pushes and
runtime restarts remain untouched. This is a 131 local development acceptance.

## Actual renewal checkpoint, 2026-10-02 11:06 UTC

The corrected native transport obtained actual game Request/Result packets, a
legal UID-10000 listener-bound ticket, no OP, the private CEF account cookie and
successful reversible skin operations. Source-access expiry triggered one actual
RpcHost refresh/DPAPI rotation; website-session expiry caused a second actual
native exchange and returned to the ordinary player center. No identity/ticket
fixture was used. Account data has Chinese text rendered normally in the saved
real screenshot. Skins are restored and disconnect revocation is recorded.

The run stopped at stage 7 because the QA simulated Esc with `setScreen(null)`,
which does not call the actual Esc/onClose path. QA now invokes the actual Esc key
handler. Production TerminalScreen.removed also clears SSO authority, so screen
replacement disarms retained cookies/pending proofs as onClose already does.
Album visibility handling remains intact. The new product artifact compiles;
the full close/reopen, in-flight reconnect, switch/restart/revoke suite remains
unverified after this repair and must be run in the next coordinated window.

## Offline lifecycle preparation after the actual renewal run

`run_terminal_navigation.py <JDK> --three-rounds` passes the original 70 and new
42 actual-Navigation lifecycle assertions. Its CEF/MC/API components are explicit
standalone unit fixtures, not native game acceptance or new native identity hooks.
The new regression first reproduced a retained-view race: an old account document
completion could fail a fresh native reopening. Navigation and the account view
now ignore only the old canonical account-document callbacks while a fresh native
payload is pending; original issuer/origin, exact-view and connection checks remain.
Old-account failure callbacks receive the same handling. Automatic reopen, double
onClose/removed, old heartbeat/native reply, reused CEF ID and revoke/history cases
pass without interactive auth navigation. Actual RpcHost unit regressions pass 13;
actual native RpcHost/Java-pipe/TLS-issuer probes pass 17. Compiled bytecode confirms
removed clears SSO before unchanged album visibility handling, and openTerminal
still resumes native authority after setting the screen. No MC/CEF is started by
these checks; all remaining real three-round states still need the next window.

## Actual three-round acceptance PASS, 2026-10-02

The actual 131 native suite passed all three rounds on the rebuilt terminal
artifact: A-to-B switch (UID 10000), fresh B MC/CEF process (UID 10001), and B source
revoke (UID 10001). Each round passed actual source-access expiry/RpcHost refresh,
website-session expiry/native renewal, actual Esc key-handler close/reopen, a ticket
request held in flight across real disconnect, a new actual connection/listener,
normal account permissions and reversible skin write/reset. The switch and revoke
rounds blanked the old view through actual native broker denial. Twelve actual
native exchange POSTs and twelve legal UID/listener-bound Result packets occurred;
six actual login and six disconnect/revoke events were recorded, with no OP and
no interactive browser auth requests. All three Minecraft processes exited 0;
the final actual logout cleared the private DPAPI session. Identity/ticket/callback
fixture mixins were absent from native mode. This is local isolated native lifecycle
acceptance, not deployment or a blanket assertion about unrelated player-center
features. Parent receipt: task-3/sso-native-three-round-pass.json.
