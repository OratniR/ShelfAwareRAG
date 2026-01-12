# src/reset_expiry.py
import sqlite3
from shelf_aware.constants import SQLITE_DB_PATH

def reset_all_items():
    print(f"Connecting to database at {SQLITE_DB_PATH}...")
    conn = sqlite3.connect(str(SQLITE_DB_PATH))
    
    with conn:
        # 全アイテムの賞味期限を NULL にし、ステータスを 0 (未推定) に戻す
        conn.execute("""
            UPDATE items 
            SET expiry_date = NULL, 
                is_estimated = 0
        """)
        
        # 念のため使用量カウントなどはリセットしない（今月のAPI使用量は維持すべきだから）
        
    print("✅ All items have been reset. expiry_date is now NULL.")

if __name__ == "__main__":
    reset_all_items()