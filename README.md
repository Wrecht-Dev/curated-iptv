# curated-iptv

Personal curated IPTV playlist: US FAST channels + iptv-org, grouped by genre
(Cartoons & Kids, True Crime, US News & Weather, History & Docs, Sci-Fi & Fantasy,
Classic Cable & Sitcoms). A GitHub Action probes every stream daily, replaces dead
URLs by re-pulling sources and name-matching, quarantines unfixable channels, and
publishes the result to GitHub Pages.

## Use in your player

| Setting | Value |
|---|---|
| M3U URL | `https://wrecht-dev.github.io/curated-iptv/curated.m3u` |
| EPG URL | `https://i.mjh.nz/all/epg.xml.gz` |

## Local use

```bash
python3 build.py           # rebuild curated.m3u from raw sources
python3 check_streams.py --dry-run   # report dead streams only
python3 check_streams.py   # probe + repair in place
```

## How it works

- `raw/` — fetched source playlists (Pluto/Plex/Samsung/Roku US + iptv-org US)
- `build.py` — genre curation: regex groups, dedupe, clean names
- `check_streams.py` — concurrent HLS-aware liveness probe; dead entries get a
  verified same-name (or fuzzy) replacement from refreshed sources; leftovers go
  to `quarantine.m3u`; backups in `backups/`; daily log in `reports/`
- `.github/workflows/daily-refresh.yml` — runs the above daily at 05:00 ET,
  commits results, deploys `site/` to Pages
