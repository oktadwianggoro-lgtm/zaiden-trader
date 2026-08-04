import sqlite3
import pandas as pd
from datetime import datetime

def test_leakage():
    conn = sqlite3.connect('d:/Saham/Aplikasi Zaiden traider/data/zaiden_trader.db')
    print("Testing ML Weekly predictions for leakage...")
    try:
        preds = pd.read_sql("SELECT * FROM ml_weekly_predictions", conn)
        print(f"Found {len(preds)} predictions.")
        if len(preds) > 0:
            print(preds.head())
    except Exception as e:
        print(f"Error: {e}")
    conn.close()

if __name__ == '__main__':
    test_leakage()
