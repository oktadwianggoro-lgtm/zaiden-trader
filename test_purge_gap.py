import sqlite3
import pandas as pd
from datetime import datetime

def verify_purge_gap():
    conn = sqlite3.connect('d:/Saham/Aplikasi Zaiden traider/data/zaiden_trader.db')
    runs = pd.read_sql("SELECT * FROM ml_weekly_model_runs", conn)
    print(f"Found {len(runs)} model runs.")
    
    for _, run in runs.iterrows():
        train_end = run.get('training_end', 'N/A')
        val_start = run.get('validation_start', 'N/A')
        print(f"Run {run['id']} ({run['model_name']}): Train End: {train_end} -> Val Start: {val_start}")
        if train_end and val_start and train_end != 'N/A' and val_start != 'N/A':
            delta = (datetime.strptime(val_start, "%Y-%m-%d") - datetime.strptime(train_end, "%Y-%m-%d")).days
            print(f"  Gap = {delta} days. (Expected > 5 for horizon=5)")
            if delta <= 5:
                print("  [ERROR] Potential leakage! Gap is too small.")
            else:
                print("  [OK] Purge gap verified.")
    conn.close()

if __name__ == '__main__':
    verify_purge_gap()
