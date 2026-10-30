#!/usr/bin/env python3
"""Trim the mjh.nz EPGs to only channels present in curated.m3u.

1. Fetch per-provider guides from i.mjh.nz (Pluto/Plex/Samsung/Roku).
2. Match mjh channels to curated channel names (normalized).
3. Write site-ready trimmed guide: epg.xml.gz (only our channels + programmes).
4. Stamp the best-matching mjh channel id into curated.m3u tvg-id attributes.

Run AFTER check_streams.py, BEFORE gen_site.py. Stdlib only.
"""
import gzip, io, os, sys, xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_streams import norm_key, parse_m3u  # shared normalization

BASE = os.path.dirname(os.path.abspath(__file__))
CURATED = os.path.join(BASE, "curated.m3u")
EPG_OUT = os.path.join(BASE, "epg.xml.gz")
SRC = [
    "https://i.mjh.nz/PlutoTV/us.xml.gz",
    "https://i.mjh.nz/Plex/us.xml.gz",
    "https://i.mjh.nz/SamsungTVPlus/us.xml.gz",
    "https://i.mjh.nz/Roku/all.xml.gz",
]

def fetch_epg():
    import io, urllib.request
    blobs = []
    for url in SRC:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "iptv-curator/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                blobs.append(r.read())
        except Exception as e:
            print(f"  ! EPG fetch failed: {url}: {e}")
    return blobs

def merge_streams(blobs):
    return [io.BytesIO(b) for b in blobs]

def main():
    from check_streams import serialize
    curated = parse_m3u(open(CURATED, encoding="utf-8").read())
    wanted = {norm_key(e["name"]) for e in curated}
    print(f"curated channels: {len(curated)}")

    data = fetch_epg()
    print(f"source EPG: {len(data)} files")

    kept_channels, kept_programmes = [], []
    prog_counts = Counter()  # mjh id -> programme count
    for bio in merge_streams(data):
        with gzip.open(bio) as f:
            for event, elem in ET.iterparse(f, events=("end",)):
                if elem.tag == "channel":
                    hit = None
                    for d in elem.findall("display-name"):
                        k = norm_key(d.text or "")
                        if k in wanted:
                            hit = k
                            break
                    if hit:
                        kept_channels.append((elem.get("id", ""), hit, elem))
                        pass          # kept: leave intact for serialization
                    else:
                        elem.clear()  # dropped: free subtree
                elif elem.tag == "programme":
                    prog_counts[elem.get("channel", "")] += 1
                    elem.clear()      # counted only; content not needed here

    # dedupe ids across provider files (first key wins per id)
    kept_ids = {}
    for cid, k, _ in kept_channels:
        kept_ids.setdefault(cid, k)
    kept_channels = [(cid, k, el) for cid, k, el in kept_channels if kept_ids.get(cid) == k]

    # programmes whose channel survived; dedupe by (channel, start)
    kept_programmes = []
    seen_prog = set()
    for bio in merge_streams(data):
        with gzip.open(bio) as f:
            for event, elem in ET.iterparse(f, events=("end",)):
                if elem.tag == "programme":
                    cid = elem.get("channel", "")
                    if cid in kept_ids and (cid, elem.get("start")) not in seen_prog:
                        seen_prog.add((cid, elem.get("start")))
                        kept_programmes.append(elem)   # kept: leave intact
                    else:
                        elem.clear()                   # dropped: free subtree

    # best (richest) mjh id per curated channel
    best = {}
    for cid, k in kept_ids.items():
        if k not in best or prog_counts[cid] > prog_counts[best[k]]:
            best[k] = cid
    chosen = set(best.values())

    # write trimmed guide: only chosen channels + their programmes
    root = ET.Element("tv", {
        "generator-info-name": "curated-iptv trimmer",
        "generator-info-url": "https://github.com/Wrecht-Dev/curated-iptv",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S %z"),
    })
    n_out_chan = n_out_prog = 0
    for cid, k, c in kept_channels:
        if cid in chosen:
            root.append(c)
            n_out_chan += 1
    for p in kept_programmes:
        if p.get("channel", "") in chosen:
            root.append(p)
            n_out_prog += 1
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with gzip.open(EPG_OUT, "wb", compresslevel=9) as f:
        f.write(xml)
    print(f"trimmed guide: {n_out_chan} channels, {n_out_prog} programmes, "
          f"{os.path.getsize(EPG_OUT)/1e6:.1f} MB gz")

    # stamp tvg-id into curated.m3u
    for e in curated:
        k = norm_key(e["name"])
        e["tvg_id"] = best.get(k, "")
    header = '#EXTM3U x-tvg-url="%s"' % "https://wrecht-dev.github.io/curated-iptv/epg.xml.gz"
    out_lines = [header]
    groups = []
    for e in curated:
        if e["group"] not in groups:
            groups.append(e["group"])
    for g in groups:
        members = [e for e in curated if e["group"] == g]
        out_lines += ["", f"# --- {g} ({len(members)}) ---"]
        for e in members:
            out_lines.append(
                f'#EXTINF:-1 tvg-id="{e.get("tvg_id","")}" tvg-name="{e["name"]}" '
                f'tvg-logo="{e["logo"]}" group-title="{e["group"]}",{e["name"]}')
            out_lines.append(e["url"])
    with open(CURATED, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")

    unmatched = [e["name"] for e in curated if not best.get(norm_key(e["name"]))]
    print(f"tvg-id stamped: {len(best) and sum(1 for e in curated if e.get('tvg_id'))}/{len(curated)}")
    if unmatched:
        print(f"no guide match ({len(unmatched)}): " + ", ".join(unmatched[:12]) +
              ("..." if len(unmatched) > 12 else ""))

if __name__ == "__main__":
    main()
