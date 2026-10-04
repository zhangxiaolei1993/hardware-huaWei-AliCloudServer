"""在生产库上确保 (session_id, client_request_id) 唯一索引存在（幂等）。"""
import sqlite3

DB_PATH = "/opt/psychology-server/psychology.db"

con = sqlite3.connect(DB_PATH)
con.execute(
    "create unique index if not exists uq_emotion_result_session_request "
    "on emotion_results(session_id, client_request_id)"
)
con.commit()

print("indexes on emotion_results:")
for row in con.execute("PRAGMA index_list('emotion_results')"):
    print(" ", row)
con.close()
