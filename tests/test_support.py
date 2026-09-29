import sqlite3
from pathlib import Path

def connect_sqlite(path, schema, timeout=5):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=timeout)
    connection.execute('PRAGMA foreign_keys = ON')
    return connection
