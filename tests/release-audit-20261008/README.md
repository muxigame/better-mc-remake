# Historical 2026-10-08 release verification code

These scripts preserve original release verification logic. They are historical audit code, not an automatically runnable regression suite or current production tooling. provenance.json records byte-identical original paths and SHA-256 hashes.

- verify_launcher128.py: installer hash, configured public-key signature, trusted comment and tamper rejection.
- verify_final_r2.py: frozen r1/r2 Core delta and client/server inventory checks; writes a temporary contract and invokes existing checks.
- check_public_status.py: Minecraft status protocol only, not player login.
- verify_server_final.py: installed hashes, retired JAR absence, singleton modules and unchanged settings.
- review_startup.py: error-message comparison against historical startup archives.
- reverify_existing_backup.py: verifies an existing cold backup; requires a stable, normally stopped source. normalStopVerified records a caller precondition, not an independent stop check.
- test-official-sidecar.py: isolated credential-free init/install-refusal probe. It modifies isolated settings and launches a backend; it is not SSO acceptance.

Do not execute against today's production paths unchanged. Historical absolute paths, relative layouts and output writes are retained. Some scripts need an approved isolated profile or prior normal stop. Revalidate prerequisites and adapt paths before use. Instances, caches, raw results and signing keys are not retained or recreated here.

Historical published evidence: workspace commit a990a238739001150b964da62c8de14868230362, docs/release-1.4.32-2026-10-08.md. Recorded SSO/official-first-cache-connect, NSIS upgrade and prior IPv4 relay limitations remain. This migration creates no new runtime acceptance evidence.

Migration checks: ast.parse for all seven Python files and SHA-256 equality with originals. No Minecraft, sidecar, network probe, backup verification or production action is executed during migration.
