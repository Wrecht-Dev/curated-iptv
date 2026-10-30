#!/usr/bin/env python3
"""Generate the Pages site: copy curated.m3u + build index.html channel guide."""
import html, json, os, shutil
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(BASE, "site")
shutil.rmtree(SITE, ignore_errors=True)
os.makedirs(SITE)
shutil.copy2(os.path.join(BASE, "curated.m3u"), os.path.join(SITE, "curated.m3u"))
epg_src = os.path.join(BASE, "epg.xml.gz")
if os.path.exists(epg_src):
    shutil.copy2(epg_src, os.path.join(SITE, "epg.xml.gz"))

groups, total = {}, 0
cur = None
for ln in open(os.path.join(BASE, "curated.m3u"), encoding="utf-8"):
    ln = ln.strip()
    if ln.startswith("# ---"):
        cur = ln.strip("#- ").strip()
        groups[cur] = []
    elif ln.startswith("#EXTINF") and cur:
        groups[cur].append(ln.split(",")[-1])
        total += 1

stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
rows = "".join(
    f"<section><h2>{html.escape(g)} <span class='n'>{len(ch)}</span></h2>"
    f"<p>{' · '.join(html.escape(c) for c in sorted(ch))}</p></section>"
    for g, ch in groups.items()
)
with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
    f.write(f"""<!doctype html><html><head><meta charset=utf-8>
<title>curated IPTV</title><style>
body{{font-family:system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;
background:#111;color:#ddd;line-height:1.5}}h1{{color:#fff}}h2{{color:#9cf;margin-top:2rem}}
.n{{background:#234;border-radius:1rem;padding:.1rem .6rem;font-size:.8em}}
a{{color:#7ad}}</style></head><body>
<h1>Curated IPTV playlist</h1>
<p><b>{total} channels</b> · auto-refreshed daily · last build {stamp}</p>
<p>Playlist: <a href="curated.m3u">curated.m3u</a> ·
EPG (trimmed to these channels): <a href="epg.xml.gz">epg.xml.gz</a></p>
{rows}</body></html>""")

print(f"site/: curated.m3u + index.html ({total} channels, {len(groups)} groups)")
