import sqlite3

connection = sqlite3.connect("cash_store.db")
cursor = connection.cursor()

tables = cursor.execute("""
    SELECT name
    FROM sqlite_master
    WHERE type = 'table'
    AND name NOT LIKE 'sqlite_%'
""").fetchall()

for table in tables:
    table_name = table[0]
    print(f"\nTable: {table_name}")

    columns = cursor.execute(
        f'PRAGMA table_info("{table_name}")'
    ).fetchall()

    for column in columns:
        print(f"  Column: {column[1]}")

connection.close()