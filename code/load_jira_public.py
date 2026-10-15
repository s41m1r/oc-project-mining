"""Stream the Public Jira Dataset (Montgomery et al., MSR 2022) into a slim SQLite DB.

The Zenodo zip holds a gzipped mongodump archive; we decompress both layers on the fly
and parse the archive format directly (no MongoDB needed).

Tables: issue (one row per issue, with start = first transition into a work status)
        link  (Temporal/Causal links only, outward direction, with creation date if logged)

Usage: python load_jira_public.py [--limit N] [source]
  source: the zip, or the directory of downloaded parts (part00, part01, ...)
"""
import re
import sqlite3
import struct
import sys
import zlib
from pathlib import Path

import bson

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "jira-public"
DB = ROOT / "data" / "jira_public.sqlite"
MEMBER = b"ThePublicJiraDataset/3. DataDump/mongodump-JiraReposAnon.archive"

# Link types of the Temporal/Causal category (names as found in the repositories).
TEMPORAL = re.compile(r"gantt|finish|start|block|depend|follow|preced|requir|sequence", re.I)
STARTED = re.compile(r"progress|develop|implement|coding|working|review|started", re.I)


def source_chunks(src):
    """Yield raw bytes of the zip, from one file or from consecutive part files."""
    files = sorted(src.glob("part*")) if src.is_dir() else [src]
    for f in files:
        with open(f, "rb") as fh:
            while chunk := fh.read(8 << 20):
                yield chunk


def archive_bytes(src):
    """Yield the decompressed mongodump archive (zip deflate -> gzip -> raw)."""
    buf, it = b"", source_chunks(src)
    hdr = -1
    while hdr < 0:  # local file header of the archive member (the name also occurs in READMEs)
        buf += next(it)
        hdr = next((m.start() - 30 for m in re.finditer(re.escape(MEMBER), buf)
                    if buf[m.start() - 30:m.start() - 26] == b"PK\x03\x04"), -1)
    nlen, xlen = struct.unpack("<HH", buf[hdr + 26:hdr + 30])
    buf = buf[hdr + 30 + nlen + xlen:]
    inflate, gunzip = zlib.decompressobj(-15), zlib.decompressobj(31)
    while True:
        out = gunzip.decompress(inflate.decompress(buf))
        if out:
            yield out
        try:
            buf = next(it)
        except StopIteration:
            return


def documents(src):
    """Yield (collection, document) from the archive stream."""
    gen = archive_bytes(src)
    buf = bytearray(next(gen))
    pos, collection, prelude = 4, None, True  # skip the archive magic number

    def need(n):
        nonlocal buf, pos
        while len(buf) - pos < n:
            buf = buf[pos:]
            pos = 0
            buf += next(gen)

    try:
        while True:
            need(4)
            (n,) = struct.unpack_from("<i", buf, pos)
            if n == -1:  # terminator: next doc is a namespace header (or ends prelude)
                pos += 4
                prelude, collection = False, None
                continue
            need(n)
            doc = bson.decode(bytes(buf[pos:pos + n]))
            pos += n
            if prelude:
                continue
            if collection is None:
                collection = doc.get("collection")
                if doc.get("EOF"):
                    collection = None
                continue
            yield collection, doc
    except (StopIteration, zlib.error):
        return


def main():
    args = sys.argv[1:]
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    rest = [a for a in args if not a.startswith("--") and not a.isdigit()]
    src = Path(rest[0]) if rest else RAW / "ThePublicJiraDataset.zip"

    if DB.exists():
        DB.unlink()
    db = sqlite3.connect(DB)
    db.executescript("""
      CREATE TABLE issue (repo TEXT, key TEXT, project TEXT, type TEXT, status TEXT,
        created TEXT, start TEXT, resolved TEXT, fix_versions TEXT, n_changes INTEGER);
      CREATE TABLE link (repo TEXT, src TEXT, dst TEXT, name TEXT, outward TEXT, created TEXT);
    """)
    issues, links, n = [], [], 0
    for repo, d in documents(src):
        f = d.get("fields") or {}
        key = d.get("key")
        histories = (d.get("changelog") or {}).get("histories") or []
        start, link_created = None, {}
        for h in histories:
            for it in h.get("items") or []:
                fld = it.get("field")
                if fld == "status" and start is None and STARTED.search(it.get("toString") or ""):
                    start = h.get("created")
                elif fld == "Link" and it.get("to"):
                    link_created.setdefault(it["to"], h.get("created"))
        issues.append((repo, key, (f.get("project") or {}).get("key"),
                       (f.get("issuetype") or {}).get("name"), (f.get("status") or {}).get("name"),
                       f.get("created"), start, f.get("resolutiondate"),
                       ",".join(v.get("name", "") for v in f.get("fixVersions") or []),
                       len(histories)))
        for l in f.get("issuelinks") or []:
            t = l.get("type") or {}
            if "outwardIssue" in l and TEMPORAL.search(t.get("name", "")):
                dst = l["outwardIssue"].get("key")
                links.append((repo, key, dst, t.get("name"), t.get("outward"), link_created.get(dst)))
        n += 1
        if n % 100000 == 0:
            db.executemany("INSERT INTO issue VALUES (?,?,?,?,?,?,?,?,?,?)", issues)
            db.executemany("INSERT INTO link VALUES (?,?,?,?,?,?)", links)
            issues, links = [], []
            print(f"{n:,} issues", flush=True)
        if limit and n >= limit:
            break
    db.executemany("INSERT INTO issue VALUES (?,?,?,?,?,?,?,?,?,?)", issues)
    db.executemany("INSERT INTO link VALUES (?,?,?,?,?,?)", links)
    db.executescript("CREATE INDEX ix_issue ON issue (key); CREATE INDEX ix_link ON link (src); CREATE INDEX ix_link_dst ON link (dst);")
    db.commit()
    print(f"done: {n:,} issues -> {DB}")


if __name__ == "__main__":
    main()
