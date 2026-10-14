# Legacy SSO QA source provenance

This contains only the 17 small source/test variants not already preserved byte-for-byte (ignoring UTF-8 BOM and line endings) in the canonical working source or retained Git history. No repository clone, runtime, model, world, cache, credential file or raw result was migrated.

These are historical, paused QA variants, not current product entrypoints. Do not apply them over current Core/Auth/Terminal files or invoke the legacy scripts as deployment tasks. Current tests and product sources remain authoritative in their existing repositories. The source-only fixture file auth-external-oauth.py preserves the unique legacy fixture variant; it is not imported by the current server.

source-migration.json records original paths, sizes and exact copied bytes. Original files remain pending coordinated cleanup. The existing fixtures' server-key literal is already present in the canonical Auth unit test and uses temporary state; no live key values are included.

The browser smoke probe is now maintained at tests/ysm-models-browser-smoke/probe.py; any future explicit test writes only to build/owner-131-ysm-browser-smoke. No tests were run during this migration.
