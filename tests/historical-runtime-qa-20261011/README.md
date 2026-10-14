# Historical isolated runtime QA source

These byte-preserved Java fixtures retain unique release/race/teleport/shop
diagnostic source from the old owned test directories. Identical existing
canonical fixtures are referenced in provenance rather than copied again.
The old Python runtime forks are superseded by scripts/local_mc_debug.py and
scripts/local_mc_linked.py; they are not introduced as another maintained runner.
No instance, world, cache, binary, signing key or raw log is migrated.
These historical fixtures require their original dependency/API profiles and
are not new runtime acceptance evidence or automatically executable tests.
