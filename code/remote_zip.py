"""List or extract members of a remote zip via HTTP range requests (no full download).
Usage: remote_zip.py URL list
       remote_zip.py URL get MEMBER OUTFILE"""
import struct, sys, zlib, urllib.request

URL = sys.argv[1]

def fetch(start, end):
    req = urllib.request.Request(URL, headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(req) as r:
        return r.read()

def size():
    req = urllib.request.Request(URL, method="HEAD")
    with urllib.request.urlopen(req) as r:
        return int(r.headers["Content-Length"])

def central_directory():
    n = size()
    tail = fetch(n - 70000, n - 1)
    # ZIP64 end of central directory locator, else classic EOCD
    i = tail.rfind(b"PK\x06\x06")
    if i >= 0:
        cd_size, cd_off = struct.unpack("<QQ", tail[i + 40:i + 56])
    else:
        i = tail.rfind(b"PK\x05\x06")
        cd_size, cd_off = struct.unpack("<II", tail[i + 12:i + 20])
    cd = fetch(cd_off, cd_off + cd_size - 1)
    entries, p = [], 0
    while cd[p:p + 4] == b"PK\x01\x02":
        method, = struct.unpack("<H", cd[p + 10:p + 12])
        csize, usize = struct.unpack("<II", cd[p + 20:p + 28])
        nlen, xlen, clen = struct.unpack("<HHH", cd[p + 28:p + 34])
        off, = struct.unpack("<I", cd[p + 42:p + 46])
        name = cd[p + 46:p + 46 + nlen].decode()
        extra = cd[p + 46 + nlen:p + 46 + nlen + xlen]
        # ZIP64 extra field: values in order of the fields set to 0xFFFFFFFF
        q = 0
        while q < len(extra):
            hid, hlen = struct.unpack("<HH", extra[q:q + 4])
            if hid == 1:
                vals = list(struct.unpack("<" + "Q" * (hlen // 8), extra[q + 4:q + 4 + hlen]))
                if usize == 0xFFFFFFFF: usize = vals.pop(0)
                if csize == 0xFFFFFFFF: csize = vals.pop(0)
                if off == 0xFFFFFFFF: off = vals.pop(0)
            q += 4 + hlen
        entries.append((name, method, csize, usize, off))
        p += 46 + nlen + xlen + clen
    return entries

if __name__ == "__main__":
    entries = central_directory()
    if sys.argv[2] == "list":
        for name, m, cs, us, off in entries:
            print(f"{cs/1e6:10.1f} MB  {us/1e6:10.1f} MB  {name}")
    else:
        member, out = sys.argv[3], sys.argv[4]
        name, method, csize, usize, off = next(e for e in entries if e[0] == member)
        head = fetch(off, off + 29)
        nlen, xlen = struct.unpack("<HH", head[26:30])
        start = off + 30 + nlen + xlen
        d = zlib.decompressobj(-15) if method == 8 else None
        with open(out, "wb") as f:
            chunk = 32 << 20
            for s in range(start, start + csize, chunk):
                data = fetch(s, min(s + chunk, start + csize) - 1)
                f.write(d.decompress(data) if d else data)
            if d: f.write(d.flush())
        print("wrote", out)
