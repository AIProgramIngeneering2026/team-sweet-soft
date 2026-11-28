from alembic import context
from sqlalchemy import URL, create_engine, pool

from sweetsoft.storage.sqlite import database_path

url = URL.create("sqlite", database=str(database_path().resolve()))

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.begin() as connection:
        # sqlite3 не начинает транзакцию автоматически для DDL.
        connection.exec_driver_sql("BEGIN")
        context.configure(connection=connection, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
