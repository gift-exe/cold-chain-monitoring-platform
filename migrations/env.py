"""Alembic environment. Migrations are written in SQL (op.execute), so there is no target metadata."""
from alembic import context
from sqlalchemy import create_engine

from coldchain.config import get_settings

engine = create_engine(get_settings().db_url("owner"))
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
