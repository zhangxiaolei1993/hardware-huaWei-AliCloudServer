"""检查生产 SQLite 数据库的表清单与行数（只读）。"""
import sqlite3

DB_PATH = "/opt/psychology-server/psychology.db"

con = sqlite3.connect(DB_PATH)
tables = [
    row[0]
    for row in con.execute(
        "select name from sqlite_master where type='table' order by name"
    )
]
print("tables:", tables)
for table in tables:
    count = con.execute(f"select count(*) from {table}").fetchone()[0]
    print(f"  {table}: {count} rows")

print("\nindexes on emotion_results:")
for row in con.execute("PRAGMA index_list('emotion_results')"):
    print(" ", row)
con.close()
