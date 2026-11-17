"""Small idempotent schema upgrade for the additive destruction columns.

The project creates its schema with Base.metadata.create_all (see
migrations/README.md). create_all never alters existing tables, so older
volumes need the additive columns below.
"""
from sqlalchemy import inspect,text

ADDITIVE_COLUMNS={
    'studies':{
        'sealed_at':'VARCHAR(40)',
        'sealed_by':'VARCHAR(120)',
        'destruction_id':'INTEGER',
    },
    'runs':{
        'destroyed_at':'VARCHAR(40)',
        'destruction_id':'INTEGER',
    },
    'destructions':{
        'target_count':'INTEGER DEFAULT 0',
        'prior_run_states':'JSON',
        'prior_review_ids':'JSON',
    },
}

def ensure_schema(engine):
    from src.config.database import Base
    # Import models so metadata knows every table before create_all.
    from src.models import user,study,observation,run,review,audit,destruction  # noqa: F401
    Base.metadata.create_all(engine)
    inspector=inspect(engine)
    tables=set(inspector.get_table_names())
    with engine.begin() as conn:
        for table,columns in ADDITIVE_COLUMNS.items():
            if table not in tables:
                continue
            existing={c['name'] for c in inspector.get_columns(table)}
            for name,ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
