"""Read a match from its public Shoot'n Score It pages: name, date, number of stages and the participants table.

One fetch of the event page and one of its participants page, made only when an admin presses the button – the same
pages anyone opens in a browser. SSI's own API needs an API key and a login, so this reads the HTML, and reads it
loosely: every <table> becomes rows of cell text and the admin page lets the admin check which column is which (the
same as pasting the table). If SSI changes its pages or refuses, the admin pastes the table instead.

    python -m club ssi https://shootnscoreit.com/event/22/12345/     shows what was read (check on the server)
"""
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser

URL_RE = re.compile(r"^https?://(?:www\.)?shootnscoreit\.com/event/(\d{1,4})/(\d{1,9})(?:/[^?#]*)?(?:[?#].*)?$")
UA = "Mozilla/5.0 (compatible; ShootingDiary-ClubAmmo/1.0; one-off import by a club admin)"
MAX_BYTES = 4 * 1024 * 1024
MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                          "dec"))}
MONTHS.update({"tammi": 1, "helmi": 2, "maalis": 3, "huhti": 4, "touko": 5, "kesä": 6, "heinä": 7, "elo": 8,
               "syys": 9, "loka": 10, "marras": 11, "joulu": 12})


class SsiError(Exception):
    pass


def parse_url(url):
    """(content type, event id) from an SSI event link (any page of the event), or None."""
    m = URL_RE.match((url or "").strip())
    return (m.group(1), m.group(2)) if m else None


class _Page(HTMLParser):
    """Title candidates, visible text and every table as rows of cell text."""
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.og = self.title = self.h1 = ""
        self.text, self.tables = [], []
        self._skip = 0
        self._in = None            # "title" / "h1" while inside one
        self._stack = []           # open tables: [rows], each row [cells], cell = [text parts]
        self.times = []            # <time datetime="…">

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "meta" and a.get("property") in ("og:title",) and a.get("content"):
            self.og = self.og or a["content"].strip()
        elif tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"])
        elif tag in ("title", "h1") and not getattr(self, tag):
            self._in = tag
        elif tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack and self._stack[-1]:
            self._stack[-1][-1].append([])
        elif tag == "br":
            self._data(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == self._in:
            self._in = None
        elif tag == "table" and self._stack:
            rows = [[_norm("".join(c)) for c in r] for r in self._stack.pop() if r]
            if rows:
                self.tables.append(rows)
        elif tag in ("td", "th", "p", "div", "li", "dd", "dt", "h2", "h3", "h4", "span"):
            self._data(" ")

    def handle_data(self, data):
        if not self._skip:
            self._data(data)

    def _data(self, s):
        if self._in:
            setattr(self, self._in, getattr(self, self._in) + s)
        self.text.append(s)
        if self._stack and self._stack[-1] and self._stack[-1][-1]:
            self._stack[-1][-1][-1].append(s)


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def parse_page(html):
    p = _Page()
    p.feed(html)
    p.close()
    return p


def _title(p):
    t = _norm(p.og) or _norm(p.h1) or _norm(p.title)
    t = re.split(r"\s+[|–—]\s+(?:Shoot.?n.?Score.?It|SSI)\b", t, flags=re.I)[0]
    return t[:80]


def find_date(text, times=()):
    """The match's first day as YYYY-MM-DD: <time datetime>, then 27.10.2026, 2026-10-27, Oct. 27, 2026, 27 Oct 2026."""
    for t in times:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t)
        if m:
            return "-".join(m.groups())
    cands = []
    for m in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", text):
        cands.append((m.start(), int(m.group(3)), int(m.group(2)), int(m.group(1))))
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        cands.append((m.start(), int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in re.finditer(r"\b([A-Za-zäö]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b", text):
        mo = MONTHS.get(m.group(1).lower()[:3]) or MONTHS.get(m.group(1).lower().rstrip("kuuta"))
        if mo:
            cands.append((m.start(), int(m.group(3)), mo, int(m.group(2))))
    for m in re.finditer(r"\b(\d{1,2})\.?\s+([A-Za-zäö]{3,9})\.?,?\s+(\d{4})\b", text):
        mo = MONTHS.get(m.group(2).lower()[:3]) or MONTHS.get(re.sub(r"kuuta$", "", m.group(2).lower()))
        if mo:
            cands.append((m.start(), int(m.group(3)), mo, int(m.group(1))))
    for _pos, y, mo, d in sorted(cands):
        if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            return "%04d-%02d-%02d" % (y, mo, d)
    return ""


def find_stages(text):
    """Number of stages: "Stages: 6", "6 stages", "6 rastia", else the highest "Stage N"."""
    for rx in (r"\b(?:stages|number of stages|stage count)\s*[:#]?\s*(\d{1,2})\b", r"\b(\d{1,2})\s+(?:stages|rastia|rasti)\b",
               r"\brastit\s*[:#]?\s*(\d{1,2})\b"):
        m = re.search(rx, text, re.I)
        if m and 0 < int(m.group(1)) <= 30:
            return int(m.group(1))
    nums = [int(x) for x in re.findall(r"\b(?:stage|rasti)\s*#?\s*(\d{1,2})\b", text, re.I) if 0 < int(x) <= 30]
    return max(nums) if nums else 0


def best_table(tables):
    """The participants table: the one with the most rows that has a name-like column."""
    good = [t for t in tables if len(t) >= 2 and max(len(r) for r in t) >= 2]
    if not good:
        return []
    named = [t for t in good if any(re.search(r"competitor|name|nimi|shooter|ampuja|participant", c, re.I)
                                    for c in t[0])]
    t = max(named or good, key=len)
    w = max(len(r) for r in t)
    # drop columns that are empty in every row (icons, buttons)
    keep = [i for i in range(w) if any(i < len(r) and r[i] for r in t)]
    return [[r[i] if i < len(r) else "" for i in keep] for r in t][:600]


def _get(url, opener=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html",
                                               "Accept-Language": "en,fi;q=0.8"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=15) as r:
            raw = r.read(MAX_BYTES + 1)
            charset = r.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as e:
        raise SsiError("SSI answered %d." % e.code)
    except (urllib.error.URLError, OSError) as e:
        raise SsiError("Couldn't reach SSI (%s)." % getattr(e, "reason", e))
    if len(raw) > MAX_BYTES:
        raise SsiError("SSI's page is too big.")
    return raw.decode(charset, "replace")


def fetch(url, opener=None):
    """{url, name, date, stages, rows} for an SSI event link. Raises SsiError."""
    ids = parse_url(url)
    if not ids:
        raise SsiError("Not a shootnscoreit.com event link.")
    base = "https://shootnscoreit.com/event/%s/%s/" % ids
    ev = parse_page(_get(base, opener))
    text = _norm(" ".join(ev.text))
    out = {"url": base, "name": _title(ev), "date": find_date(text, ev.times), "stages": find_stages(text), "rows": []}
    try:
        part = parse_page(_get(base + "participants/", opener))
        out["rows"] = best_table(part.tables)
    except SsiError as e:
        out["warning"] = str(e)
    if not out["rows"]:
        out["rows"] = best_table(ev.tables) if any(len(t) > 3 for t in ev.tables) else []
    if not (out["name"] or out["rows"]):
        raise SsiError("Couldn't read the match from SSI – paste the table instead.")
    return out
