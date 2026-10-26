"""Read a match from Shoot'n Score It's GraphQL API: name, date, number of stages, competitors and their squads.

SSI wants an API key (header x-api-key) and a login token (Authorization: JWT …) on every call. The token comes from
logging in with an SSI account (token_auth); it's kept in memory and fetched again when SSI says it has expired.
All three go in the server's .env – never in the repo, never to the browser:

    SSI_API_KEY=…            the API key SSI gave the club
    SSI_EMAIL=…              an SSI account (a club / service account is best)
    SSI_PASSWORD=…

Only called when an admin presses Fetch. Check on the server:  venv/bin/python -m club ssi <event link>
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

ENDPOINT = "https://shootnscoreit.com/graphql/"
URL_RE = re.compile(r"^https?://(?:www\.)?shootnscoreit\.com/event/(\d{1,4})/(\d{1,9})(?:/[^?#]*)?(?:[?#].*)?$")
TOKEN_TTL = 25 * 60                    # SSI's tokens live longer; fetch a new one well before
EXPIRED = ("Signature has expired", "Error decoding signature", "expired", "Invalid token")

LOGIN = ("mutation Login($email: String!, $pwd: String!) { token_auth(email: $email, password: $pwd) "
         "{ success errors token { token } } }")
MATCH = """query ClubMatch($ct: Int!, $id: String!) {
  event(content_type: $ct, id: $id) {
    id name starts
    ... on IpscMatchNode {
      stages_count
      competitors_approved_w_wo_results_not_dnf {
        id
        ... on IpscCompetitorNode { first_name last_name number get_division_display category }
      }
      squads { id ... on IpscSquadNode { number get_squad_display competitors { id } } }
    }
  }
}"""

_lock = threading.Lock()
_token = {"jwt": "", "at": 0.0}


class SsiError(Exception):
    pass


def configured():
    return all(os.environ.get(k) for k in ("SSI_API_KEY", "SSI_EMAIL", "SSI_PASSWORD"))


def parse_url(url):
    """(content type, event id) from an SSI event link (any page of the event), or None."""
    m = URL_RE.match((url or "").strip())
    return (m.group(1), m.group(2)) if m else None


def _post(query, variables, jwt=None, opener=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "x-api-key": os.environ.get("SSI_API_KEY", ""), "User-Agent": "ShootingDiary-ClubAmmo/1.0"}
    if jwt:
        headers["Authorization"] = "JWT " + jwt
    req = urllib.request.Request(ENDPOINT, data=json.dumps({"query": query, "variables": variables}).encode(),
                                 headers=headers, method="POST")
    try:
        with (opener or urllib.request.urlopen)(req, timeout=30) as r:
            body = json.loads(r.read(8 * 1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SsiError("SSI answered %d%s." % (e.code, " – check SSI_API_KEY" if e.code in (401, 403) else ""))
    except (urllib.error.URLError, OSError) as e:
        raise SsiError("Couldn't reach SSI (%s)." % getattr(e, "reason", e))
    except ValueError:
        raise SsiError("SSI sent something that isn't JSON.")
    errs = body.get("errors") or []
    if errs:
        raise SsiError("SSI: " + "; ".join(str(e.get("message", e)) for e in errs)[:300])
    return body.get("data") or {}


def _login(opener=None):
    d = _post(LOGIN, {"email": os.environ.get("SSI_EMAIL", ""), "pwd": os.environ.get("SSI_PASSWORD", "")},
              opener=opener).get("token_auth") or {}
    tok = (d.get("token") or {}).get("token")
    if not d.get("success") or not tok:
        raise SsiError("SSI login failed – check SSI_EMAIL / SSI_PASSWORD.")
    _token.update(jwt=tok, at=time.time())
    return tok


def query(q, variables, opener=None):
    """Run a query with a valid token; log in again once if SSI says the token has expired."""
    if not configured():
        raise SsiError("SSI API isn't set up: SSI_API_KEY, SSI_EMAIL and SSI_PASSWORD go in the server's .env.")
    with _lock:
        jwt = _token["jwt"] if time.time() - _token["at"] < TOKEN_TTL else _login(opener)
    try:
        return _post(q, variables, jwt, opener)
    except SsiError as e:
        if not any(x.lower() in str(e).lower() for x in EXPIRED):
            raise
        with _lock:
            jwt = _login(opener)
        return _post(q, variables, jwt, opener)


def _local_date(starts):
    """SSI's start time (ISO, usually UTC) as the local date YYYY-MM-DD."""
    s = str(starts or "")
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (d.astimezone() if d.tzinfo else d).strftime("%Y-%m-%d")
    except ValueError:
        return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else ""


def fetch(url, opener=None):
    """{url, name, date, stages, competitors: [{num, name, squad, division}]} for an SSI event link."""
    ids = parse_url(url)
    if not ids:
        raise SsiError("Not a shootnscoreit.com event link.")
    ev = query(MATCH, {"ct": int(ids[0]), "id": ids[1]}, opener).get("event")
    if not ev:
        raise SsiError("SSI has no match behind that link (or this account can't see it).")
    squad_of = {}
    for sq in ev.get("squads") or []:
        label = str(sq.get("number") or "") or re.sub(r"^(squad|ryhmä)\s*", "", str(sq.get("get_squad_display") or ""),
                                                      flags=re.I)
        for c in sq.get("competitors") or []:
            squad_of[str(c.get("id"))] = label
    comps = []
    for c in ev.get("competitors_approved_w_wo_results_not_dnf") or []:
        name = " ".join(x for x in (str(c.get("first_name") or "").strip(), str(c.get("last_name") or "").strip()) if x)
        if len(name) >= 2:
            comps.append({"num": str(c.get("number") or ""), "name": name, "squad": squad_of.get(str(c.get("id")), ""),
                          "division": str(c.get("get_division_display") or ""),
                          "category": str(c.get("category") or "")[:60]})       # e.g. Special 2 = borrows a pistol
    comps.sort(key=lambda c: (c["squad"] == "", int(c["squad"]) if c["squad"].isdigit() else 0, c["squad"], c["name"]))
    return {"url": "https://shootnscoreit.com/event/%s/%s/" % ids, "name": str(ev.get("name") or "")[:80],
            "date": _local_date(ev.get("starts")), "stages": int(ev.get("stages_count") or 0), "competitors": comps}
