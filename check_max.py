import sqlite3; conn=sqlite3.connect('data/zaiden_trader.db'); print(conn.execute('SELECT MAX(date) FROM idx_prices').fetchone())
