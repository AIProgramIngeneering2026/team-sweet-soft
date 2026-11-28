from alembic import context
from sqlalchemy import create_engine, pool

from sweetsoft.storage.settings import database_url

url = database_url()

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.begin() as connection:
        if connection.dialect.name == "sqlite":
            # sqlite3 не начинает транзакцию автоматически для DDL.
            connection.exec_driver_sql("BEGIN")
        context.configure(connection=connection, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
