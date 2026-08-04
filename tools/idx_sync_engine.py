import argparse
import sys
import os
from pathlib import Path
from datetime import date, datetime, timedelta
import threading
import sqlite3
import pandas as pd
import json
import hashlib
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db import migrate
from tools.sync_idx_daily import iter_weekdays, IdxSession, RateLimiter, fetch_day, save_result

DB_PATH = ROOT / "data" / "zaiden_trader.db"

def backfill_trading_summary(start_date, end_date, max_workers=3, max_days=None):
    print(f"Starting adaptive backfill from {start_date} to {end_date}")
    from tools.sync_idx_daily import pending_days, FetchResult
    
    connection = sqlite3.connect(DB_PATH, timeout=60)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 60000")
    
    days = list(pending_days(connection, start_date, end_date, refresh=3))
    if max_days:
        days = days[:max_days]
        
    print(f"Total missing/pending days to backfill: {len(days)}")
    
    if not days:
        connection.close()
        return 0
        
    limiter = RateLimiter(1.0)
    local = threading.local()
    
    def run_fetch(day: date) -> FetchResult:
        if not hasattr(local, "session"):
            local.session = IdxSession(30.0, limiter)
        return fetch_day(local.session, day, retries=5)
    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    success = empty = failed = inserted = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_day = {executor.submit(run_fetch, day): day for day in days}
        for future in as_completed(future_to_day):
            day = future_to_day[future]
            try:
                result = future.result()
                save_result(connection, result)
                completed += 1
                if result.status == "success":
                    success += 1
                    inserted += len(result.rows)
                elif result.status == "no_data":
                    empty += 1
                else:
                    failed += 1
                print(f"[{completed}/{len(days)}] {result.day} {result.status}: {len(result.rows)} rows", flush=True)
            except Exception as e:
                print(f"Error fetching {day}: {e}")
                failed += 1

    connection.commit()
    connection.close()
    return failed

def get_screener_snapshot(session):
    url = "https://www.idx.co.id/primary/StockData/GetStockScreener"
    print("Fetching fundamental screener...")
    try:
        status, ctype, body = session.get(url)
        return status, body.decode()
    except Exception as e:
        return 503, str(e)
    
def sync_fundamental_screener():
    from tools.sync_idx_daily import IdxSession, RateLimiter
    connection = sqlite3.connect(DB_PATH, timeout=60)
    
    limiter = RateLimiter(1.0)
    session = IdxSession(30.0, limiter)
    
    today = date.today().isoformat()
    
    status, body = get_screener_snapshot(session)
    if status == 200:
        try:
            payload = json.loads(body)
            data = payload.get('data', [])
            payload_hash = hashlib.sha256(body.encode('utf-8')).hexdigest()
            
            last_hash = connection.execute("SELECT payload_sha256 FROM idx_fundamental_sync_log ORDER BY tanggal DESC LIMIT 1").fetchone()
            
            if last_hash and last_hash[0] == payload_hash:
                connection.execute("INSERT OR REPLACE INTO idx_fundamental_sync_log (tanggal, status, records_total, payload_sha256, fetched_at, updated_at) VALUES (?, 'no_change', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (today, len(data), payload_hash))
            else:
                for item in data:
                    stock_code = item.get('KodeSaham', item.get('StockCode', ''))
                    # We will parse all fields later if we can get a valid response
                connection.execute("INSERT OR REPLACE INTO idx_fundamental_sync_log (tanggal, status, records_total, payload_sha256, fetched_at, updated_at) VALUES (?, 'success', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (today, len(data), payload_hash))
                
        except Exception as e:
            connection.execute("INSERT OR REPLACE INTO idx_fundamental_sync_log (tanggal, status, error, fetched_at, updated_at) VALUES (?, 'failed', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (today, str(e)))
    else:
        connection.execute("INSERT OR REPLACE INTO idx_fundamental_sync_log (tanggal, status, http_status, error, fetched_at, updated_at) VALUES (?, 'failed', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (today, status, body))

    connection.commit()
    connection.close()

def calc_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period - 1, adjust=False).mean()
    ema_down = down.ewm(com=period - 1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

def calc_macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    macd_hist = macd - macd_signal
    return macd, macd_signal, macd_hist

def calc_bb(series, window=20, num_sd=2):
    rolling_mean = series.rolling(window=window).mean()
    rolling_std = series.rolling(window=window).std()
    upper = rolling_mean + (rolling_std * num_sd)
    lower = rolling_mean - (rolling_std * num_sd)
    return upper, rolling_mean, lower

def calc_stoch(high, low, close, k_window=14, d_window=3):
    min_low = low.rolling(window=k_window).min()
    max_high = high.rolling(window=k_window).max()
    stoch_k = 100 * (close - min_low) / (max_high - min_low)
    stoch_d = stoch_k.rolling(window=d_window).mean()
    return stoch_k, stoch_d

def calc_atr(high, low, close, window=14):
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/window, adjust=False).mean()
    return atr

def calculate_derived_features():
    print("Calculating Zaiden BDM Intelligence Features...")
    connection = sqlite3.connect(DB_PATH, timeout=60)
    
    # In production, we'd limit this to recent dates if it gets too slow, but doing the whole thing ensures historical accuracy for MA/MACD.
    df = pd.read_sql("SELECT * FROM ringkasan_saham_harian ORDER BY tanggal ASC", connection)
    if df.empty:
        connection.close()
        return
        
    print(f"Loaded {len(df)} rows for feature calculation.")
    
    import numpy as np
    
    df['tanggal_idx'] = pd.to_datetime(df['tanggal'])
    df = df.sort_values(by=['kode_saham', 'tanggal_idx'])
    
    gb = df.groupby('kode_saham')
    
    # 1. TECHNICAL INDICATORS
    print("Calculating Technical Indicators (RSI, MACD, BB, STOCH, ATR)...")
    df['rsi14'] = gb['harga_penutupan'].transform(lambda x: calc_rsi(x, 14))
    
    # MACD returns a tuple of series, so we calculate and assign
    macd_res = df.groupby('kode_saham')['harga_penutupan'].apply(lambda x: pd.DataFrame(dict(zip(['macd', 'signal', 'hist'], calc_macd(x))))).reset_index(level=0, drop=True)
    df['macd'] = macd_res['macd']
    df['macd_signal'] = macd_res['signal']
    df['macd_hist'] = macd_res['hist']
    
    # SMACD (2, 10, 2)
    smacd_res = df.groupby('kode_saham')['harga_penutupan'].apply(lambda x: pd.DataFrame(dict(zip(['macd', 'signal', 'hist'], calc_macd(x, 2, 10, 2))))).reset_index(level=0, drop=True)
    df['smacd_macd'] = smacd_res['macd']
    df['smacd_signal'] = smacd_res['signal']
    
    bb_res = df.groupby('kode_saham')['harga_penutupan'].apply(lambda x: pd.DataFrame(dict(zip(['upper', 'mid', 'lower'], calc_bb(x))))).reset_index(level=0, drop=True)
    df['bb_upper'] = bb_res['upper']
    df['bb_mid'] = bb_res['mid']
    df['bb_lower'] = bb_res['lower']
    
    stoch_res = df.groupby('kode_saham').apply(lambda g: pd.DataFrame(dict(zip(['k', 'd'], calc_stoch(g['harga_tertinggi'], g['harga_terendah'], g['harga_penutupan']))))).reset_index(level=0, drop=True)
    df['stoch_k'] = stoch_res['k']
    df['stoch_d'] = stoch_res['d']
    
    atr_res = df.groupby('kode_saham').apply(lambda g: calc_atr(g['harga_tertinggi'], g['harga_terendah'], g['harga_penutupan'])).reset_index(level=0, drop=True)
    df['atr14'] = atr_res
    df['atr_pct'] = (df['atr14'] / df['harga_penutupan']) * 100
    
    df['return_1d'] = gb['harga_penutupan'].pct_change()
    df['volatility'] = gb['return_1d'].transform(lambda x: x.rolling(20).std() * np.sqrt(252))
    
    # 2. LIQUIDITY METRICS
    print("Calculating Liquidity Metrics...")
    df['med_val_20'] = gb['nilai_transaksi'].transform(lambda x: x.rolling(20).median())
    df['med_freq_20'] = gb['frekuensi'].transform(lambda x: x.rolling(20).median())
    
    # Avoid division by zero
    val_nonzero = df['nilai_transaksi'].replace(0, np.nan)
    df['amihud'] = df['return_1d'].abs() / val_nonzero
    df['amihud_20'] = gb['amihud'].transform(lambda x: x.rolling(20).mean())
    
    df['turnover_ratio_20'] = gb.apply(lambda g: (g['volume'] / g['saham_tercatat'].replace(0, np.nan)).rolling(20).mean()).reset_index(level=0, drop=True)
    
    # Liquidity Score (0-100) based on Median Value and Frequency
    # Rough rank: Val > 10B = liquid, Freq > 1000 = liquid.
    val_score = (df['med_val_20'] / 10_000_000_000).clip(0, 1) * 50
    freq_score = (df['med_freq_20'] / 1000).clip(0, 1) * 50
    df['liquidity_score'] = val_score + freq_score
    df['is_liquid'] = df['liquidity_score'] > 50
    df['liquidity_level'] = np.where(df['liquidity_score'] >= 80, 'HIGH', np.where(df['liquidity_score'] >= 50, 'MEDIUM', 'LOW'))
    
    # 3. FLOW METRICS (Foreign)
    print("Calculating Flow Metrics...")
    df['fnet_1d'] = df['beli_asing'] - df['jual_asing']
    df['fnet_20d'] = gb['fnet_1d'].transform(lambda x: x.rolling(20).sum())
    
    val_20d = gb['nilai_transaksi'].transform(lambda x: x.rolling(20).sum())
    df['fnet_20d_pct'] = (df['fnet_20d'] / (val_20d * 2).replace(0, np.nan)) * 100
    
    # Clean up NaN for SQLite
    df = df.replace([np.nan, np.inf, -np.inf], None)
    
    print("Saving to database...")
    connection.execute("BEGIN TRANSACTION")
    
    # Insert Technical
    tech_cols = ['kode_saham', 'tanggal', 'atr14', 'atr_pct', 'rsi14', 'stoch_k', 'stoch_d', 'macd', 'macd_signal', 'macd_hist', 'bb_upper', 'bb_mid', 'bb_lower', 'volatility', 'smacd_macd', 'smacd_signal']
    tech_data = df[tech_cols].dropna(subset=['rsi14', 'macd']).to_dict('records')
    connection.executemany('''
        INSERT OR REPLACE INTO stock_technical_eod (
            security_id, trade_date, atr14, atr_pct, rsi14, stoch_k, stoch_d, 
            macd, macd_signal, macd_hist, bb_upper, bb_mid, bb_lower, 
            volatility, smacd_macd, smacd_signal, calculation_version
        ) VALUES (
            :kode_saham, :tanggal, :atr14, :atr_pct, :rsi14, :stoch_k, :stoch_d,
            :macd, :macd_signal, :macd_hist, :bb_upper, :bb_mid, :bb_lower,
            :volatility, :smacd_macd, :smacd_signal, 'v1'
        )
    ''', tech_data)
    
    # Insert Liquidity
    liq_cols = ['kode_saham', 'tanggal', 'med_val_20', 'med_freq_20', 'turnover_ratio_20', 'amihud_20', 'liquidity_score', 'liquidity_level', 'is_liquid']
    liq_data = df[liq_cols].dropna(subset=['med_val_20']).to_dict('records')
    connection.executemany('''
        INSERT OR REPLACE INTO stock_liquidity_metrics (
            security_id, as_of_date, median_value_20d, median_frequency_20d, 
            turnover_ratio_20d, amihud_20d, liquidity_score, liquidity_level, is_liquid
        ) VALUES (
            :kode_saham, :tanggal, :med_val_20, :med_freq_20,
            :turnover_ratio_20, :amihud_20, :liquidity_score, :liquidity_level, :is_liquid
        )
    ''', liq_data)
    
    # Insert Flow (Foreign 20D)
    flow_cols = ['kode_saham', 'tanggal', 'fnet_20d', 'fnet_20d_pct']
    flow_data = df[flow_cols].dropna(subset=['fnet_20d']).to_dict('records')
    # Transform dicts to add method/period
    flow_final = []
    for row in flow_data:
        flow_final.append({
            'security_id': row['kode_saham'],
            'as_of_date': row['tanggal'],
            'method_code': 'FOREIGN',
            'period_code': '20D',
            'net_value': row['fnet_20d'],
            'net_pct': row['fnet_20d_pct']
        })
    connection.executemany('''
        INSERT OR REPLACE INTO stock_flow_metrics (
            security_id, as_of_date, method_code, period_code, 
            net_value, net_pct_of_total_value, calculation_version
        ) VALUES (
            :security_id, :as_of_date, :method_code, :period_code,
            :net_value, :net_pct, 'v1'
        )
    ''', flow_final)
    
    connection.commit()
    connection.close()
    print("Features calculation successfully written to DB.")
    
def daily_sync_job():
    print("Running Daily Sync Job...")
    backfill_trading_summary(date.today(), date.today())
    sync_fundamental_screener()
    calculate_derived_features()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--daily", action="store_true")
    parser.add_argument("--start", type=str, default="2020-01-02")
    parser.add_argument("--end", type=str, default=date.today().isoformat())
    args = parser.parse_args()
    
    migrate()
    
    if args.backfill:
        backfill_trading_summary(
            date.fromisoformat(args.start), 
            date.fromisoformat(args.end)
        )
        calculate_derived_features()
        
    if args.daily:
        daily_sync_job()
    
if __name__ == '__main__':
    main()
