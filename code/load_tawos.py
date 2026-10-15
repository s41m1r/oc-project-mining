"""Convert the TAWOS MySQL dump into SQLite without needing MySQL.

Streams TAWOS.sql straight out of the zip (no 4.3 GB extraction), parses
CREATE TABLE and INSERT statements, and writes every table to SQLite.

Usage: python3 load_tawos.py [zip_path] [sqlite_path]
"""
import io
import re
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
ZIP_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA / "raw" / "TAWOS.sql.zip"
DB_PATH = Path(sys.argv[2]) if len(sys.argv) > 2 else DATA / "tawos.sqlite"

COLUMN = re.compile(r"^\s+`(\w+)`\s+(\w+)")
# Tokens inside an INSERT ... VALUES list; commas between tokens are skipped.
TOKEN = re.compile(r"\(|\)|'(?:[^'\\]|\\.)*'|NULL|[^,()'\s;]+")
ESCAPE = re.compile(r"\\(.)", re.S)
ESCAPES = {"0": "\0", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a", "b": "\b"}

INDEXES = {
    "Issue": ["ID", "Project_ID", "Sprint_ID", "Issue_Key"],
    "Issue_Link": ["Issue_ID", "Target_Issue_ID"],
    "Change_Log": ["Issue_ID", "Field"],
    "Comment": ["Issue_ID"],
    "Fix_Version": ["Issue_ID", "Fix_Version_ID"],
    "Affected_Version": ["Issue_ID"],
    "Issue_Component": ["Issue_ID", "Component_ID"],
    "Version": ["ID", "Project_ID"],
    "Sprint": ["ID", "Project_ID"],
    "Component": ["ID"],
    "Project": ["ID"],
}


def sqlite_type(mysql_type):
    t = mysql_type.lower()
    if t in ("int", "tinyint", "smallint", "bigint"):
        return "INTEGER"
    if t in ("double", "float", "decimal"):
        return "REAL"
    return "TEXT"


def value(tok):
    if tok[0] == "'":
        s = tok[1:-1]
        return ESCAPE.sub(lambda m: ESCAPES.get(m.group(1), m.group(1)), s) if "\\" in s else s
    if tok == "NULL":
        return None
    try:
        return int(tok)
    except ValueError:
        return float(tok)


def rows(values_text):
    row = None
    for m in TOKEN.finditer(values_text):
        tok = m.group()
        if tok == "(":
            row = []
        elif tok == ")":
            yield row
        else:
            row.append(value(tok))


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")

    counts, table, columns = {}, None, []
    start = time.time()
    with zipfile.ZipFile(ZIP_PATH) as zf, zf.open("TAWOS.sql") as raw:
        for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace"):
            if line.startswith("CREATE TABLE"):
                table, columns = line.split("`")[1], []
            elif table and columns is not None and (m := COLUMN.match(line)):
                if not line.lstrip().startswith(("KEY", "CONSTRAINT", "PRIMARY", "UNIQUE")):
                    columns.append((m.group(1), sqlite_type(m.group(2))))
            elif line.startswith(") ENGINE") and table:
                cols = ", ".join(f'"{c}" {t}' for c, t in columns)
                db.execute(f'CREATE TABLE "{table}" ({cols})')
                counts[table] = 0
                columns = None
            elif line.startswith("INSERT INTO"):
                name = line.split("`")[1]
                batch = list(rows(line[line.index(" VALUES ") + 8:]))
                marks = ", ".join("?" * len(batch[0]))
                db.executemany(f'INSERT INTO "{name}" VALUES ({marks})', batch)
                counts[name] += len(batch)
                if counts[name] % 200000 < len(batch):
                    print(f"{time.time() - start:7.0f}s  {name}: {counts[name]:,}", flush=True)

    for name, cols in INDEXES.items():
        for col in cols:
            db.execute(f'CREATE INDEX "ix_{name}_{col}" ON "{name}" ("{col}")')
    db.commit()
    db.close()
    for name, n in counts.items():
        print(f"{name:20s} {n:>12,}")
    print(f"done in {time.time() - start:.0f}s -> {DB_PATH}")


if __name__ == "__main__":
    main()
