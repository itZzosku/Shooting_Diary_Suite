#!/usr/bin/env python3
"""One-off: look at what Shoot'n Score It's GraphQL API offers, so the diary integration can be built on real
field names. Run on the server (it can reach shootnscoreit.com):

    ./venv/bin/python scripts/ssi_probe.py

Reads SSI_API_KEY, SSI_EMAIL and SSI_PASSWORD from .env (never printed or written out). Writes ssi-probe.json
next to the code: the API schema (type and field names only) and the field names of your own profile – no
values that identify you except what you choose to share. Send that file back.
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
except ImportError:
    pass

URL = "https://shootnscoreit.com/graphql/"
KEY, EMAIL, PWD = (os.getenv(k, "").strip() for k in ("SSI_API_KEY", "SSI_EMAIL", "SSI_PASSWORD"))
if not (KEY and EMAIL and PWD):
    sys.exit("Add SSI_API_KEY, SSI_EMAIL and SSI_PASSWORD to .env first.")


def gql(query, variables=None, jwt=None):
    h = {"Content-Type": "application/json", "x-api-key": KEY, "User-Agent": "shooting-diary-probe"}
    if jwt:
        h["Authorization"] = "JWT " + jwt
    req = urllib.request.Request(URL, json.dumps({"query": query, "variables": variables or {}}).encode(), h)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


login = gql("mutation L($e:String!,$p:String!){ token_auth(email:$e,password:$p){ success errors token{token} } }",
            {"e": EMAIL, "p": PWD})
ta = (login.get("data") or {}).get("token_auth") or {}
if not ta.get("success"):
    sys.exit(f"Login failed: {ta.get('errors') or login.get('errors')}")
jwt = ta["token"]["token"]
print("Logged in.")

TYPE = "kind name ofType { kind name ofType { kind name ofType { kind name } } }"
schema = gql("""{ __schema { queryType { name } types { kind name
    fields { name args { name type { %s } } type { %s } }
    possibleTypes { name } enumValues { name } } } }""" % (TYPE, TYPE), jwt=jwt)
if schema.get("errors"):
    sys.exit(f"Schema query refused: {schema['errors']}")
types = [t for t in schema["data"]["__schema"]["types"] if not t["name"].startswith("__")]

# Which of the 'me' fields can be read (names only, values left out)
me_type = next((f["type"] for t in types if t["name"] == schema["data"]["__schema"]["queryType"]["name"]
                for f in (t["fields"] or []) if f["name"] == "me"), None)
out = {"query_type": schema["data"]["__schema"]["queryType"]["name"], "types": types, "me_type": me_type}
path = os.path.join(HERE, "ssi-probe.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=1)

interesting = [t["name"] for t in types if any(w in t["name"].lower() for w in ("sra", "competitor", "match", "score", "result", "shooter"))]
print(f"{len(types)} types written to {path}")
print("Relevant types:", ", ".join(sorted(interesting)[:60]))
