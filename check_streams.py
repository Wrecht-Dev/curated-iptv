#!/usr/bin/env python3
"""Daily dead-stream checker + auto-replacer for curated.m3u.

Flow:
  1. Parse curated playlist.
  2. Probe every stream URL concurrently (HLS-aware).
  3. Refresh raw source playlists (unless --offline).
  4. For each dead channel: find a live replacement URL by name-match
     (exact first, then fuzzy) and verify the candidate actually plays.
  5. Replace what's fixable; quarantine the rest.
  6. Backup, rewrite playlist, write report.

Stdlib only. Run manually:  ./check_streams.py
Report-only (no writes):    ./check_streams.py --dry-run
Skip source refresh:        ./check_streams.py --offline
Built-in logic tests:       ./check_streams.py --self-test
"""
import argparse, difflib, os, re, shutil, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

HOME = os.path.expanduser("~")
BASE = os.environ.get("IPTV_HOME") or os.path.dirname(os.path.abspath(__file__))
PLAYLIST = os.path.join(BASE, "curated.m3u")
RAW_DIR = os.path.join(BASE, "raw")
QUARANTINE = os.path.join(BASE, "quarantine.m3u")
REPORT_DIR = os.path.join(BASE, "reports")
BACKUPS = os.path.join(BASE, "backups")
KEEP_BACKUPS = 5

UA = "VLC/3.0.20 LibVLC/3.0.20"

RAW_SOURCES = [
    "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plutotv_us.m3u",
    "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plex_us.m3u",
    "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/samsungtvplus_us.m3u",
    "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/roku_all.m3u",
    "https://iptv-org.github.io/iptv/countries/us.m3u",
]

TAG = re.compile(
    r"\s*\((?:\d{3,4}p|United States|US|Not 24/7)\)|\s*\[(?:Geo-blocked|Not 24/7)\]", re.I
)

# ---------------------------------------------------------------- parsing

def clean_name(name):
    name = name.replace("\u2019", "'").replace("\u2018", "'").replace(" & ", " and ")
    prev = None
    while prev != name:
        prev = name
        name = TAG.sub("", name).strip()
    return re.sub(r"\s+", " ", name)

def norm_key(name):
    return re.sub(r"[^a-z0-9]+", "", clean_name(name).lower())

def parse_m3u(text):
    """-> list of dicts: name, logo, group, url (raw order preserved)."""
    entries, extinf = [], None
    for ln in text.splitlines():
        ln = ln.strip()
        if ln.startswith("#EXTINF"):
            extinf = ln
        elif extinf and ln and not ln.startswith("#"):
            stripped = re.sub(r'\s+\S+="[^"]*"', "", extinf)
            name = stripped.split(",", 1)[-1].strip()
            m = re.search(r'tvg-logo="([^"]*)"', extinf)
            g = re.search(r'group-title="([^"]*)"', extinf)
            i = re.search(r'tvg-id="([^"]*)"', extinf)
            entries.append({
                "name": name,
                "logo": m.group(1) if m else "",
                "group": g.group(1) if g else "Uncategorized",
                "tvg_id": i.group(1) if i else "",
                "url": ln,
            })
            extinf = None
    return entries

def serialize(entries, header="#EXTM3U x-tvg-url=\"https://i.mjh.nz/all/epg.xml.gz,https://i.mjh.nz/PlutoTV/us.xml.gz,https://i.mjh.nz/Plex/us.xml.gz,https://i.mjh.nz/SamsungTVPlus/us.xml.gz,https://i.mjh.nz/Roku/all.xml.gz\""):
    out, seen_groups = [header], []
    for e in entries:
        if e["group"] not in seen_groups:
            seen_groups.append(e["group"])
    for g in seen_groups:
        members = [e for e in entries if e["group"] == g]
        out += ["", f"# --- {g} ({len(members)}) ---"]
        for e in members:
            out.append(f'#EXTINF:-1 tvg-id="{e.get("tvg_id", "")}" tvg-name="{e["name"]}" '
                       f'tvg-logo="{e["logo"]}" '
                       f'group-title="{e["group"]}",{e["name"]}')
            out.append(e["url"])
    return "\n".join(out) + "\n"

# ---------------------------------------------------------------- probing

def looks_live(status, ctype, body):
    """Liveness decision from raw response facts (unit-testable)."""
    if status in (401, 403, 404, 410, 451):          # hard-dead signals
        return False
    if status != 200:
        return False
    low = ctype.lower()
    head = body[:256].lstrip().lower()
    if b"#extm3u" in head:
        return True
    if "mpegurl" in low or "x-mpegurl" in low or "vnd.apple" in low:
        return True
    if low.startswith("video/") or low == "application/octet-stream":
        return True
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return False                                  # error page masquerading
    return False

def fetch(url, timeout, max_bytes=262144):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read(max_bytes), r.geturl()

def first_variant(body, base_url):
    """If body is a master playlist, return the first variant URL."""
    lines = body.decode("utf-8", "replace").splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("#EXT-X-STREAM-INF"):
            m = re.search(r'URI="([^"]+)"', ln)
            if m:
                return urllib.parse.urljoin(base_url, m.group(1))
            for nxt in lines[i + 1:]:
                if nxt.strip() and not nxt.startswith("#"):
                    return urllib.parse.urljoin(base_url, nxt.strip())
    return None

def probe(url, timeout=12, retries=2):
    """True if the stream responds with a usable manifest/media."""
    for attempt in range(retries):
        try:
            status, ctype, body, final = fetch(url, timeout)
            if not looks_live(status, ctype, body):
                return False
            if b"#EXT-X-STREAM-INF" in body:          # master -> check a variant
                v = first_variant(body, final)
                if v and v != url:
                    try:
                        s2, c2, b2, _ = fetch(v, timeout)
                        return looks_live(s2, c2, b2)
                    except Exception:
                        return False
            return True
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404, 410, 451):
                return False
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    return False

# ---------------------------------------------------------------- sources

def refresh_raw(timeout=60):
    os.makedirs(RAW_DIR, exist_ok=True)
    for url in RAW_SOURCES:
        dest = os.path.join(RAW_DIR, re.sub(r"[^A-Za-z0-9._-]", "_", url.split("/")[-1]))
        try:
            _, _, data, _ = fetch(url, timeout)
            with open(dest, "wb") as f:
                f.write(data)
        except Exception as e:
            print(f"  ! refresh failed for {url}: {e}")

def load_raw_index():
    """norm_key -> list of replacement URLs from all raw playlists."""
    index = {}
    for fn in sorted(os.listdir(RAW_DIR)):
        if not fn.endswith((".m3u", ".m3u8")):
            continue
        try:
            with open(os.path.join(RAW_DIR, fn), encoding="utf-8", errors="replace") as f:
                for e in parse_m3u(f.read()):
                    index.setdefault(norm_key(e["name"]), []).append(e["url"])
        except OSError:
            pass
    return index

def find_replacement(name, index, min_ratio):
    """Return a candidate URL (not yet verified) for a dead channel name."""
    k = norm_key(name)
    if k and k in index:
        return index[k][0], 1.0
    best, ratio = None, 0.0
    for cand in index:
        r = difflib.SequenceMatcher(None, k, cand).ratio()
        if r > ratio:
            best, ratio = cand, r
    if best and ratio >= min_ratio:
        return index[best][0], ratio
    return None, ratio

# ---------------------------------------------------------------- backup

def rotate_backup(path):
    os.makedirs(BACKUPS, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, os.path.join(BACKUPS, f"curated.{stamp}.m3u"))
    olds = sorted(os.listdir(BACKUPS))
    for old in olds[:-KEEP_BACKUPS]:
        os.remove(os.path.join(BACKUPS, old))

# ---------------------------------------------------------------- main

def run(args):
    with open(args.playlist, encoding="utf-8") as f:
        header_line = f.readline().strip()
    entries = parse_m3u(open(args.playlist, encoding="utf-8").read())
    if header_line.startswith("#EXTM3U"):
        global_hdr = header_line
    else:
        global_hdr = None
    print(f"Probing {len(entries)} channels ({args.workers} workers, {args.timeout}s timeout)...")

    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(probe, e["url"], args.timeout): e for e in entries}
        for fut in as_completed(futs):
            e = futs[fut]
            results[e["url"]] = fut.result()

    dead = [e for e in entries if not results[e["url"]]]
    print(f"  alive: {len(entries) - len(dead)}   dead: {len(dead)}")
    if not dead:
        write_report(len(entries), [], [], args.dry_run)
        print("All streams healthy. Nothing to do.")
        return

    if args.dry_run:
        print("\nDEAD (dry run, no changes):")
        for e in dead:
            print(f"  [{e['group']}] {e['name']}  {e['url']}")
        return

    if not args.offline:
        print("Refreshing source playlists...")
        refresh_raw()
    index = load_raw_index()
    print(f"Replacement index: {len(index)} channel names from {RAW_DIR}")

    replaced, quarantined, keep = [], [], []
    for e in entries:
        if results[e["url"]]:
            keep.append(e)
            continue
        cand, ratio = find_replacement(e["name"], index, args.min_ratio)
        if cand and cand != e["url"] and probe(cand, args.timeout):
            old = e["url"]
            e["url"] = cand
            keep.append(e)
            replaced.append((e["name"], old, cand, ratio))
            print(f"  replaced: {e['name']} (match {ratio:.0%})")
        else:
            quarantined.append(e)
            print(f"  quarantined: {e['name']}")

    # write playlist in original order, stable groups
    order = {id(e): i for i, e in enumerate(entries)}
    keep.sort(key=lambda e: order[id(e)])
    rotate_backup(args.playlist)
    with open(args.playlist, "w", encoding="utf-8") as f:
        f.write(serialize(keep, global_hdr or serialize.__defaults__[0]))
    with open(QUARANTINE, "a", encoding="utf-8") as f:
        f.write(serialize(quarantined))
    write_report(len(entries), dead, replaced, False, quarantined)

def write_report(total, dead, replaced, dry, quarantined=None):
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"{datetime.now():%Y-%m-%d}.txt")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                f"total={total} dead={len(dead)} replaced={len(replaced)} "
                f"quarantined={len(quarantined or [])}{' (dry-run)' if dry else ''}\n")
        for name, old, new, r in replaced:
            f.write(f"  REPLACED {name}\n    old: {old}\n    new: {new} (match {r:.0%})\n")
        for e in (quarantined or []):
            f.write(f"  QUARANTINED [{e['group']}] {e['name']}  {e['url']}\n")
    print(f"Report: {path}")

def self_test():
    # name cleaning / normalization
    assert clean_name("Cartoon Network (United States) (720p) [Geo-blocked]") == "Cartoon Network"
    assert norm_key("Nick-Jr. Pluto TV (720p)") == norm_key("Nick Jr Pluto TV")
    # parse/serialize roundtrip
    sample = ('#EXTM3U\n# --- G1 (1) ---\n'
              '#EXTINF:-1 tvg-name="A" tvg-logo="l.png" group-title="G1",A\nhttp://x/a.m3u8\n')
    es = parse_m3u(sample)
    assert len(es) == 1 and es[0]["name"] == "A" and es[0]["group"] == "G1"
    assert "A" in serialize(es) and "http://x/a.m3u8" in serialize(es)
    # liveness logic
    assert looks_live(200, "application/vnd.apple.mpegurl", b"#EXTM3U\n#EXTINF:6,\nseg.ts")
    assert looks_live(200, "video/mp2t", b"\x47\x40\x00\x10")
    assert not looks_live(404, "text/html", b"#EXTM3U")
    assert not looks_live(200, "text/html", b"<!doctype html><html>error</html>")
    assert not looks_live(403, "application/vnd.apple.mpegurl", b"#EXTM3U")
    # master-variant extraction
    master = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nvid/720p.m3u8\n'
    assert first_variant(master, "http://h/a.m3u8") == "http://h/vid/720p.m3u8"
    # fuzzy replacement
    fake = {norm_key("Cartoon Network West"): ["http://c/w"], norm_key("Boomerang"): ["http://b/"]}
    url, r = find_replacement("Cartoon Network West", fake, 0.85)
    assert url == "http://c/w" and r == 1.0
    url, r = find_replacement("Cartoon Network East", fake, 0.85)
    assert url == "http://c/w" and r >= 0.85
    assert find_replacement("Totally Unrelated Show", fake, 0.85)[0] is None
    print("self-test: all assertions passed")

def main():
    global PLAYLIST
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--playlist", default=PLAYLIST)
    p.add_argument("--raw-dir", default=RAW_DIR)
    p.add_argument("--timeout", type=int, default=12)
    p.add_argument("--workers", type=int, default=20)
    p.add_argument("--min-ratio", type=float, default=0.85)
    p.add_argument("--offline", action="store_true", help="use cached raw playlists")
    p.add_argument("--dry-run", action="store_true", help="report dead streams only")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    PLAYLIST = args.playlist
    if args.raw_dir != RAW_DIR:
        globals()["RAW_DIR"] = args.raw_dir
    if args.self_test:
        self_test()
        return
    run(args)

if __name__ == "__main__":
    main()
