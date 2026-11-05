"""Rellena la base de datos con 25 tareas de ejemplo: python scripts/seed.py"""
import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parent.parent / "tasks.db"
conn = sqlite3.connect(db)
conn.execute("DROP TABLE IF EXISTS tasks")
conn.execute(
    """CREATE TABLE tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'todo',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        priority TEXT NOT NULL DEFAULT 'medium'
    )"""
)
for i in range(1, 26):
    status = "done" if i % 3 == 0 else "todo"
    conn.execute(
        "INSERT INTO tasks (title, description, status) VALUES (?, ?, ?)",
        (f"Tarea {i}", f"Descripción de la tarea {i}", status),
    )
conn.commit()
conn.close()
print("25 tareas creadas en", db)
