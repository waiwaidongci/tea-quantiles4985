Schema v1 is defined by the SQLAlchemy models and created at startup via Base.metadata.create_all. For this local small application no destructive migration is run. Back up named db-data before modifying schemas.

Schema v2 adds the authorization-withdrawal tables/columns additively:

- `destructions` (new): seal + destruction task, status `pending|cleaning|completed|failed`, history, certificate.
- `studies.sealed_at`, `studies.sealed_by`, `studies.destruction_id`: the seal marker checked by every entry point.
- `runs.destroyed_at`, `runs.destruction_id`: per-run scrub marker.

`src/config/schema.py:ensure_schema` runs at API startup, in the worker and in the seed script. It calls `create_all` and then adds any missing additive column with `ALTER TABLE ... ADD COLUMN`; it never drops or rewrites data. Existing volumes therefore upgrade in place without a destructive migration. Back up named `db-data` first, as before.
