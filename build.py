#!/usr/bin/env python3
"""Build a genre-curated M3U from raw FAST/iptv-org playlists."""
import re, glob, os

RAW = os.environ.get("IPTV_HOME") or os.path.expanduser("~/iptv-cur")
RAW = os.path.join(RAW, "raw")
OUT = os.path.join(os.environ.get("IPTV_HOME") or os.path.expanduser("~/iptv-cur"), "curated.m3u")

GROUPS = [
    ("Cartoons & Kids", [
        r"cartoon", r"boomerang", r"nick", r"disney", r"tom and jerry", r"looney",
        r"spongebob", r"hasbro", r"my little pony", r"baby shark", r"care bears",
        r"strawberry shortcake", r"hello kitty", r"peanuts", r"smurf",
        r"woody woodpecker", r"\bkids\b", r"treehouse", r"tooncast", r"manga",
        r"anime", r"retrocrush", r"toonami", r"pucca", r"qubo", r"teletubbies",
        r"ketchup", r"moonbug", r"lego",
    ]),
    ("True Crime", [
        r"court tv", r"crime", r"forensic", r"investigation", r"ion mystery",
        r"law & crime", r"law and crime", r"true crime", r"american crimes",
        r"diagnosis murder", r"midsomer", r"detective", r"unsolved", r"cold case",
        r"snapped", r"\bmurder", r"crimes cults", r"crime scenes", r"crime zone",
        r"reelz", r"bloodline", r"new detectives", r"people magazine",
    ]),
    ("US News & Weather", [
        r"abc news", r"cbs news", r"nbc news", r"fox news", r"fox weather",
        r"accuweather", r"weather", r"\bcnn\b", r"msnbc", r"newsy", r"bloomberg",
        r"cnbc", r"newsmax", r"oann", r"euronews", r"france 24", r"al jazeera",
        r"\bdw\b", r"c-?span", r"reuters", r"sky news", r"\bnews\b", r"nasa",
        r"nbc (los angeles|kptv)",
    ]),
    ("History & Docs", [
        r"history", r"smithsonian", r"documentary", r"curiosity", r"discovery",
        r"animal planet", r"nat geo", r"national geographic", r"military",
        r"popular science", r"bbc earth", r"bbc america", r"antiques roadshow",
        r"pawn stars", r"modern marvels",
    ]),
    ("Sci-Fi & Fantasy", [
        r"star trek", r"stargate", r"doctor who", r"bbc sci-fi", r"twilight zone",
        r"outer limits", r"sci-?fi", r"science fiction", r"x-files", r"tokusatsu",
        r"pluto tv science", r"mystery science theater", r"\bmst3k\b",
        r"tokushoutaru", r"midnight pulp", r"filmrise sci", r"\bgenre\b",
    ]),
    ("Classic Cable & Sitcoms", [
        r"\bmtv\b", r"vh1", r"comedy central", r"paramount", r"tv land", r"metv",
        r"\bget\b.*tv", r"bet ", r"black family sitcoms", r"classic tv",
        r"sitcom", r"family feud", r"game show", r"funniest", r"\bafv\b",
        r"i love lucy", r"cheers", r"frasier", r"seinfeld", r"\bmash\b",
        r"happy days", r"laverne", r"andy griffith", r"mayberry", r"gunsmoke",
        r"bonanza", r"western", r"classic movie", r"cine retro", r"artflix",
        r"cinevault", r"cinemax", r"page six", r"tmz", r"\be! ", r"\be!$",
        r"\ba&e\b", r"we tv", r"reality", r"\bamc\b", r"\btnt\b", r"\btbs\b",
        r"\bfx\b", r"usa network", r"hallmark", r"\bbravo\b", r"challenge",
        r"yo! mtv", r"club mtv", r"mtv ", r"duck dynasty", r"storage wars",
        r"the first 48", r"intervention", r"hoarders", r"60 days in",
        r"kids in the hall", r"halloween", r"mgm", r"stories by amc",
        r"universal", r"\bfilmrise\b", r"absolute reality", r"all.?out reality",
        r"love after lockup", r"bravo vault", r"chaos on cam",
    ]),
]

EXCLUDE = re.compile(
    r"scientolog|alkarma|genesis science|3abn|praise|discipleship|bible|church|worship|"
    r"islamic|quran|lottery|bingo|casino|shopping|qvc|hsn|jewelry|sommelier|\bwine\b|"
    r"tony robbins|logos tv|news12|cj enm|30a|kpvm|"
    r"espanol|espa\u00f1ol|espa\u00f1a|latino|pel\u00edculas|gratis|cr\u00edmenes|autopista|"
    r"concursos|series gratis|noticias|asesinatos|nuevos detectives|todo crimen|crimen\b|"
    r"\barabic\b|news of the world|promz|zoom news|bek news|revry|filamtv|estrella|"
    r"curiosity animales|curiosity explora|curiosity motores|cnn chile|"
    r"^abc news live \d+$|bloomberg tv live event|bloomberg tv politics|cbs news philly\b|"
    r"bonanza-billies|mtv flow latino|sumtv",
    re.I,
)

TAG = re.compile(r"\s*\((?:\d{3,4}p|United States|US|Not 24/7)\)|\s*\[(?:Geo-blocked|Not 24/7)\]", re.I)

def clean(name):
    """Normalize apostrophes/ampersands, strip tags -> clean display + dedupe key."""
    name = name.replace("\u2019", "'").replace("\u2018", "'").replace(" & ", " and ")
    prev = None
    while prev != name:
        prev = name
        name = TAG.sub("", name).strip()
    return re.sub(r"\s+", " ", name)

def parse(path):
    entries = []
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    extinf = None
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("#EXTINF"):
            extinf = ln
        elif extinf and ln and not ln.startswith("#"):
            entries.append((extinf, ln))
            extinf = None
    return entries

def get_name(extinf):
    # strip attributes so commas in display names survive
    stripped = re.sub(r'\s+\S+="[^"]*"', "", extinf)
    return stripped.split(",", 1)[-1].strip()

def get_logo(extinf):
    m = re.search(r'tvg-logo="([^"]*)"', extinf)
    return m.group(1) if m else ""

def main():
    seen = {}          # dedupe-key -> (clean_name, extinf, url)
    for gname, pats in GROUPS:
        for path in sorted(glob.glob(os.path.join(RAW, "*.m3u"))):
            for extinf, url in parse(path):
                raw_name = get_name(extinf)
                n = clean(raw_name)
                key = n.lower()
                if not n or EXCLUDE.search(n):
                    continue
                if key in seen:
                    continue
                if not any(re.search(p, n, re.I) for p in pats):
                    continue
                seen[key] = (n, extinf, url, gname)

    # regroup + sort
    buckets = {g: [] for g, _ in GROUPS}
    for key, (n, extinf, url, gname) in seen.items():
        logo = get_logo(extinf)
        e = (f'#EXTINF:-1 tvg-name="{n}" tvg-logo="{logo}" '
             f'group-title="{gname}",{n}')
        buckets[gname].append((n.lower(), e, url))

    out = ['#EXTM3U x-tvg-url="https://i.mjh.nz/all/epg.xml.gz,https://i.mjh.nz/PlutoTV/us.xml.gz,https://i.mjh.nz/Plex/us.xml.gz,https://i.mjh.nz/SamsungTVPlus/us.xml.gz,https://i.mjh.nz/Roku/all.xml.gz"']
    total = 0
    for gname, _ in GROUPS:
        chans = sorted(buckets[gname])
        total += len(chans)
        out.append("")
        out.append(f"# --- {gname} ({len(chans)}) ---")
        for _, e, u in chans:
            out.append(e)
            out.append(u)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"Wrote {OUT}")
    for gname, _ in GROUPS:
        print(f"  {gname}: {len(buckets[gname])}")
    print(f"  TOTAL: {total}")

if __name__ == "__main__":
    main()
