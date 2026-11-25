#!/usr/bin/env python3
"""Auto-Wächter

Sucht neue Occasionen (tutti.ch direkt, dazu Suchabo-Mails von AutoScout24, tutti,
Comparis, Ricardo …) und meldet passende Autos per ntfy-Push. Die Suchaufträge
("Wächter") stehen in waechter.json und lassen sich über docs/index.html ändern.

Umgebungsvariablen:
  HOME_LAT, HOME_LON  Koordinaten, ab denen der Umkreis gemessen wird (Pflicht)
  NTFY_TOPIC          geheimer ntfy-Kanal (Pflicht, ausser bei --dry-run)
  NTFY_SERVER         Standard: https://ntfy.sh
  SEITE               Basis-URL der GitHub-Pages-Seiten (optional), z. B.
                      https://<benutzer>.github.io/auto-waechter/
  STATE_PATH          Standard: state/state.json
  MAIL_USER, MAIL_APP_PASSWORD  Postfach mit Suchabo-Mails (optional, Mail-Brücke)
  MAIL_IMAP_HOST      Standard: imap.gmail.com
  MAIL_ORDNER         Standard: Auto-Alarm

Aufruf:
  python3 waechter.py             normaler Lauf
  python3 waechter.py --dry-run   nichts senden, nichts speichern, Treffer anzeigen
  python3 waechter.py --test-push Beispiel-Push senden
  python3 waechter.py --alle      alle aktuell passenden Autos als Liste senden
  python3 waechter.py --mail-datei a.eml b.eml   Suchabo-Mails testweise auswerten (lokal)
"""
import base64
import json
import math
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (compatible; auto-waechter/1.0)"
TZ = ZoneInfo("Europe/Zurich")

# Auf GitHub sind die Logs öffentlich: dort keine Treffer mit Orten und Distanzen ausgeben.
PUBLIC_LOG = os.environ.get("GITHUB_ACTIONS") == "true"


# ---------------------------------------------------------------- Hilfen

def log(*a):
    print(datetime.now(TZ).strftime("%H:%M:%S"), *a, flush=True)


def http(url, data=None, headers=None, timeout=25):
    h = {"User-Agent": UA, "Accept-Language": "de-CH,de;q=0.9"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        # Ältere macOS-Pythons scheitern an manchen TLS-Servern; dann curl nehmen.
        if data is not None or "SSL" not in str(e.reason):
            raise
        cmd = ["curl", "-sfL", "--max-time", str(timeout)]
        for k, v in h.items():
            cmd += ["-H", "%s: %s" % (k, v)]
        return subprocess.run(cmd + [url], check=True, capture_output=True).stdout.decode("utf-8", "replace")


def get_json(url):
    return json.loads(http(url, headers={"Accept": "application/json"}))


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def chf(n):
    return "{:,}".format(int(n)).replace(",", "'")


def airline_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- Auto-Daten erkennen

BRANDS = {  # Schreibweise im Inserat → Name im Wächter
    "skoda": "Skoda", "vw": "VW", "volkswagen": "VW", "audi": "Audi", "subaru": "Subaru",
    "toyota": "Toyota", "volvo": "Volvo", "bmw": "BMW", "mercedes-benz": "Mercedes-Benz",
    "mercedes": "Mercedes-Benz", "ford": "Ford", "mazda": "Mazda", "dacia": "Dacia",
    "hyundai": "Hyundai", "kia": "Kia", "seat": "Seat", "cupra": "Cupra", "opel": "Opel",
    "peugeot": "Peugeot", "renault": "Renault", "citroen": "Citroen", "nissan": "Nissan",
    "honda": "Honda", "mitsubishi": "Mitsubishi", "suzuki": "Suzuki", "mini": "Mini",
    "porsche": "Porsche", "jeep": "Jeep", "land rover": "Land Rover", "tesla": "Tesla", "fiat": "Fiat",
}
BRAND_RE = re.compile(r"(?<![a-z])(%s)(?![a-z])" % "|".join(sorted(map(re.escape, BRANDS), key=len, reverse=True)))

# Modelle, bei denen der Name im Titel anders steht als im Katalog
MODEL_RE = {
    ("VW", "Golf Variant"): r"golf\b.*\b(variant|kombi)\b|golf variant",
    ("VW", "Passat Variant"): r"passat\b.*\b(variant|kombi|alltrack)\b|passat variant|passat alltrack",
    ("Audi", "A4 Avant"): r"\ba4\b.*\b(avant|allroad)\b",
    ("Audi", "A6 Avant"): r"\ba6\b.*\b(avant|allroad)\b",
    ("BMW", "3er Touring"): r"\b(3er|3\d\d[a-z]{0,2})\b.*touring|touring.*\b3\d\d",
    ("BMW", "5er Touring"): r"\b(5er|5\d\d[a-z]{0,2})\b.*touring|touring.*\b5\d\d",
    ("Mercedes-Benz", "C-Klasse T-Modell"): r"\b(c[ -]?\d{3}|c-klasse|c klasse)\b.*\b(t-modell|t modell|kombi|estate|t)\b",
    ("Mercedes-Benz", "E-Klasse T-Modell"): r"\b(e[ -]?\d{3}|e-klasse|e klasse)\b.*\b(t-modell|t modell|kombi|estate|t)\b",
    ("Toyota", "Corolla Touring Sports"): r"corolla\b.*\b(touring|ts|kombi)\b",
    ("Ford", "Focus Station Wagon"): r"focus\b.*\b(station|kombi|turnier|sw|wagon)\b",
    ("Mazda", "6 Kombi"): r"(mazda ?6|\b6\b).*\b(kombi|wagon|sport ?break|sportbreak)\b",
}

KOMBI_RE = re.compile(r"\b(combi|kombi|variant|avant|touring|break|station ?wagon|wagon|estate|sportswagon|sports ?tourer"
                      r"|tourer|t-modell|turnier|allroad|alltrack|shooting ?brake|scout|outback|levorg|v60|v70|v90|sw|st)\b")
SUV_RE = re.compile(r"\b(suv|gelandewagen|kodiaq|karoq|kamiq|tiguan|touareg|t-roc|q2|q3|q5|q7|x1|x3|x5|rav ?4|xc40|xc60|xc90"
                    r"|glc|gla|glb|kuga|cx-?3|cx-?5|duster|tucson|sportage|forester|xv|crosstrek|qashqai|x-trail|yeti)\b")
LIMO_RE = re.compile(r"\b(limousine|lim\.|sedan|berline)\b")
AWD_RE = re.compile(r"4x4|4 x 4|allrad|4motion|quattro|xdrive|4matic|\bawd\b|\ball4\b|\bq4\b|\b4wd\b|allgrip|alldrive"
                    r"|\bscout\b|alltrack|allroad|e-four|\bawd\b")
AWD_BRANDS_ALWAYS = {"Subaru"}  # Subaru: praktisch nur mit Allrad
DIESEL_RE = re.compile(r"\b(tdi|cdi|crdi|dci|hdi|tdci|bluehdi|multijet|jtd|diesel|d4|d5|\d\.\d ?d|\d{3}d|skyactiv-d)\b")
HYBRID_RE = re.compile(r"hybrid|phev|plug-in")
EV_RE = re.compile(r"\b(elektro|electric|e-tron|\bev\b|iv)\b")
BENZIN_RE = re.compile(r"\b(tsi|tfsi|fsi|tgi|benzin|t-gdi|gdi|ecoboost|vtec|skyactiv-g|tce|puretech|\d\.\di|\d{3}i)\b")
AUTO_RE = re.compile(r"\b(dsg|automat\w*|automatik|aut\.?|s ?tronic|tiptronic|steptronic|cvt|lineartronic|geartronic"
                     r"|\dg-tronic|dct|edc|eat\d|powershift|7g|9g)\b")
MANUAL_RE = re.compile(r"\b(schaltgetriebe|manuell|handschalt\w*|\d-gang)\b")
MFK_RE = re.compile(r"ab mfk|frisch (ab )?mfk|mfk (neu|frisch)|neu mfk|frisch vorgef|ab platz|mfk \d{1,2}[./]\d{2,4}")
KM_RE = re.compile(r"(\d{1,3}(?:['’. ]\d{3})+|\d{4,6})\s?km\b", re.I)
YEAR_RES = [
    re.compile(r"(?:erstzulassung|1\. ?inverkehrsetzung|inverkehrsetzung|1\. ?inv\.?|jahrgang|jg\.?|ez)\s*:?\s*(?:\d{1,2}[./])?((?:19|20)\d{2})"),
    re.compile(r"\b(?:0?[1-9]|1[0-2])[./]((?:19|20)\d{2})\b"),
    re.compile(r"\b((?:19|20)\d{2})\s*[·|,/–-]\s*\d{1,3}(?:['’. ]\d{3})+\s?km\b"),  # "2017 · 119'000 km"
]
PRICE_RE = re.compile(r"(?:CHF|Fr\.)\s*([\d'’.]{3,9})(?:\.[\-–—]|\.00)?|([\d'’]{3,9})\.[\-–—]", re.I)
PLZ_RE = re.compile(r"\b([1-9]\d{3})\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüéèàâç.\-]+(?: [A-ZÄÖÜ][A-Za-zÄÖÜäöüéè.\-]+)?)")
NOT_CAR_RE = re.compile(r"felge|reifen|rader|räder|ersatzteil|steuergerat|turbolader|motor\b|getriebe zu verk|spiegel|scheinwerfer"
                        r"|stossstange|dachbox|dachtrager|anhangerkupplung zu|modellauto|miniatur|teile|zubehor|matten")


def to_int(s):
    d = re.sub(r"\D", "", s or "")
    return int(d) if d else None


def detect(title, body="", props=None):
    """Aus Titel, Text und (falls vorhanden) strukturierten Angaben die Auto-Daten lesen.
    Unbekanntes bleibt None."""
    props = props or {}
    t = norm(title)
    full = norm((title or "") + " \n " + (body or ""))
    car = {}

    b = props.get("Marke")
    m = BRAND_RE.search(norm(b) if b else t) or BRAND_RE.search(full)
    car["brand"] = BRANDS[m.group(1)] if m else None
    car["model_text"] = norm(" ".join(x for x in [props.get("Marke"), props.get("Modell"), title] if x))

    a = norm(props.get("Aufbau"))
    if a:
        car["body"] = ("Kombi" if "kombi" in a else "SUV" if ("suv" in a or "gelande" in a) else
                       "Limousine" if "limousine" in a else "Van" if "van" in a else
                       "Kompakt" if ("kompakt" in a or "klein" in a) else a.title())
    else:
        car["body"] = "Kombi" if KOMBI_RE.search(t) else "SUV" if SUV_RE.search(t) else "Limousine" if LIMO_RE.search(t) else None

    car["awd"] = bool(AWD_RE.search(full)) or car["brand"] in AWD_BRANDS_ALWAYS

    f = norm(props.get("Treibstoff"))
    if f:
        car["fuel"] = ("Hybrid" if "hybrid" in f else "Diesel" if "diesel" in f else
                       "Elektro" if "elektr" in f else "Benzin" if "benzin" in f else f.title())
    else:
        car["fuel"] = ("Hybrid" if HYBRID_RE.search(full) else "Diesel" if DIESEL_RE.search(t) else
                       "Elektro" if EV_RE.search(t) else "Benzin" if BENZIN_RE.search(t) else
                       "Diesel" if DIESEL_RE.search(full) else "Benzin" if BENZIN_RE.search(full) else None)

    g = norm(props.get("Getriebeart"))
    if g:
        car["gear"] = "Automat" if "automat" in g else "Manuell" if ("schalt" in g or "manuell" in g) else None
    else:
        car["gear"] = "Automat" if AUTO_RE.search(full) else "Manuell" if MANUAL_RE.search(full) else None

    y = to_int(props.get("Erstzulassung"))
    if not y:
        for r in YEAR_RES:
            mm = r.search(full)
            if mm:
                y = int(mm.group(1))
                break
    car["year"] = y if y and 1980 <= y <= datetime.now().year + 1 else None

    km = to_int(props.get("Kilometerstand"))
    if km is None:
        for mm in KM_RE.finditer(full):
            v = to_int(mm.group(1))
            if v and v >= 1000:
                km = v
                break
    car["km"] = km

    car["ps"] = to_int(props.get("Leistung"))
    car["mfk"] = bool(MFK_RE.search(full))
    return car


def model_ok(car, brand, model):
    if car.get("brand") != brand:
        return False
    if not model:
        return True
    text = car.get("model_text") or ""
    pat = MODEL_RE.get((brand, model))
    if pat:
        return bool(re.search(pat, text))
    return all(re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(w), text) for w in norm(model).split())


def wants_model(w, car):
    return not w["models"] or any(model_ok(car, m["brand"], m.get("model") or "") for m in w["models"])


def check(l, w):
    """Gründe, warum ein Auto nicht zum Wächter passt (leer = passt) und offene Punkte."""
    r, unsure = [], []
    if not wants_model(w, l):
        r.append("Modell nicht im Wächter")
    if w.get("body"):
        if l.get("body") is None:
            unsure.append("Aufbau unklar")
        elif l["body"] not in w["body"]:
            r.append(l["body"])
    if w.get("awd") and not l.get("awd"):
        r.append("kein 4x4")
    if l.get("price") is None:
        unsure.append("Preis unbekannt")
    elif l["price"] > w["maxPrice"]:
        r.append("über Budget")
    if w.get("maxKm"):
        if l.get("km") is None:
            unsure.append("km unbekannt")
        elif l["km"] > w["maxKm"]:
            r.append("zu viele km")
    if w.get("minYear"):
        if l.get("year") is None:
            unsure.append("Jahrgang unbekannt")
        elif l["year"] < w["minYear"]:
            r.append("zu alt")
    if w.get("fuel"):
        if l.get("fuel") is None:
            unsure.append("Treibstoff unklar")
        elif l["fuel"] not in w["fuel"]:
            r.append(l["fuel"])
    if w.get("gear", "egal") != "egal":
        if l.get("gear") is None:
            unsure.append("Getriebe unklar")
        elif l["gear"] != w["gear"]:
            r.append(l["gear"])
    if w.get("radius"):
        if l.get("dist") is None:
            unsure.append("Ort unbekannt")
        elif l["dist"] > w["radius"]:
            r.append("zu weit weg")
    if w.get("seller", "egal") != "egal" and l.get("seller") and l["seller"] != w["seller"]:
        r.append(l["seller"])
    if w.get("mfk") and not l.get("mfk"):
        r.append("nicht ab MFK")
    return r, unsure


def matching(cfg, l):
    hits = []
    for w in cfg["waechter"]:
        if not w.get("on"):
            continue
        r, unsure = check(l, w)
        if not r:
            hits.append(w)
            l["unsure"] = unsure
    return hits


def signature(l):
    if l.get("km") and l.get("year"):
        return "%s|%s|%s|%s" % (l.get("brand"), l["year"], round(l["km"] / 1000), (l.get("plz") or "")[:2])
    return "%s|%s" % (re.sub(r"[^a-z0-9]", "", norm(l.get("title")))[:40], l.get("price"))


# ---------------------------------------------------------------- Konfiguration und Zustand

def load_config():
    with open(os.path.join(ROOT, "waechter.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    try:
        cfg["home"] = {"lat": float(os.environ["HOME_LAT"]), "lon": float(os.environ["HOME_LON"])}
    except (KeyError, ValueError):
        sys.exit("HOME_LAT und HOME_LON fehlen oder sind ungültig.")
    for w in cfg["waechter"]:
        w.setdefault("models", [])
        w.setdefault("maxPrice", 10 ** 9)
        w.setdefault("push", "sofort")
        w.setdefault("drops", True)
    return cfg


def state_path():
    return os.environ.get("STATE_PATH", os.path.join(ROOT, "state", "state.json"))


def load_state():
    try:
        with open(state_path(), encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    for k, v in (("seen", {}), ("cars", {}), ("sigs", {}), ("queue", []), ("evening", []), ("fails", {}), ("mails", {})):
        s.setdefault(k, v)
    return s


def save_state(s):
    now = time.time()
    s["seen"] = {k: v for k, v in s["seen"].items() if v > now - 90 * 86400}
    s["cars"] = {k: v for k, v in s["cars"].items() if v.get("t", 0) > now - 60 * 86400}
    s["sigs"] = {k: v for k, v in s["sigs"].items() if v > now - 90 * 86400}
    s["mails"] = {k: v for k, v in s["mails"].items() if v > now - 10 * 86400}
    p = state_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


# ---------------------------------------------------------------- Quelle: tutti.ch
# tutti zeigt auch die Händler-Inserate von AutoScout24. Neueste Inserate stehen zuoberst.

def next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("tutti: Seitenformat hat sich geändert")
    return json.loads(m.group(1))


def tutti_listings(html):
    found = []

    def walk(o):
        if isinstance(o, dict):
            L = o.get("listings")
            if isinstance(L, dict) and "edges" in L:
                found.extend(e["node"] for e in L["edges"] if "node" in e)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(next_data(html))
    return found


def tutti_detail(lid):
    """Strukturierte Angaben (Marke, Modell, Erstzulassung, km, Aufbau, Treibstoff, Getriebe) und Standort."""
    d = next_data(http("https://www.tutti.ch/de/vi/%s" % lid))
    props, coords, body, ext = {}, None, "", None

    def walk(o):
        nonlocal coords, body, ext
        if isinstance(o, dict):
            if isinstance(o.get("properties"), list) and o["properties"] and isinstance(o["properties"][0], dict) \
                    and "listingPropertyID" in o["properties"][0]:
                for p in o["properties"]:
                    if p.get("label") and p.get("text"):
                        props[p["label"]] = p["text"]
            c = o.get("coordinates")
            if isinstance(c, dict) and c.get("latitude") and coords is None:
                coords = (c["latitude"], c["longitude"])
            loc = o.get("localization")
            if isinstance(loc, dict) and len(loc.get("body") or "") > len(body):
                body = loc["body"]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)
    return props, coords, body


def _msgpack(o):
    if o is None:
        return b"\xc0"
    if isinstance(o, str):
        b = o.encode("utf-8")
        return (bytes([0xa0 | len(b)]) if len(b) < 32 else b"\xd9" + bytes([len(b)])) + b
    if isinstance(o, list):
        return bytes([0x90 | len(o)]) + b"".join(_msgpack(x) for x in o)
    raise TypeError(o)


def tutti_search_url(query, page=1):
    """Suche nur in der Rubrik Autos, neueste zuerst. tutti kodiert Suchbegriff und Rubrik
    als MessagePack in der Adresse: [Suchbegriff, Rubrik, [4 weitere Filter]]."""
    token = "A" + base64.urlsafe_b64encode(_msgpack([query, "cars", [None, None, None, None]])).decode().rstrip("=")
    return "https://www.tutti.ch/de/q/autos/%s%s" % (token, "?page=%d" % page if page > 1 else "")


TUTTI_QUERY = {  # Modelle, deren Katalogname nicht im Titel steht
    ("BMW", "3er Touring"): "bmw touring", ("BMW", "5er Touring"): "bmw touring",
    ("Mercedes-Benz", "C-Klasse T-Modell"): "mercedes c", ("Mercedes-Benz", "E-Klasse T-Modell"): "mercedes e",
    ("Mazda", "6 Kombi"): "mazda 6",
}


def tutti_queries(cfg):
    qs = []
    for w in cfg["waechter"]:
        if not w.get("on"):
            continue
        for m in w["models"]:
            q = TUTTI_QUERY.get((m["brand"], m.get("model") or ""))
            if not q:
                q = norm(m.get("model") or m["brand"])
                q = re.sub(r"\b(touring sports|station wagon|t-modell|variant|avant|touring|kombi)\b", "", q).strip() or q
            if q not in qs:
                qs.append(q)
    return qs


def source_tutti(cfg, state):
    first = not state.get("initialized")
    pages = 4 if first else 1
    out, cand = [], {}
    queries = tutti_queries(cfg)
    if not queries:
        log("tutti: kein Wächter mit Marke oder Modell, übersprungen")
    for q in queries:
        for page in range(1, pages + 1):
            html = http(tutti_search_url(q, page))
            for n in tutti_listings(html):
                if (n.get("primaryCategory") or {}).get("categoryID") != "cars":
                    continue
                key = "tutti:%s" % n.get("listingID")
                price = to_int(n.get("formattedPrice"))
                if key in state["cars"]:
                    old = state["cars"][key]
                    if price and old.get("price") and price < old["price"]:
                        l = dict(old, prev=old["price"], price=price, event="drop")
                        out.append(l)
                    continue
                if key in state["seen"] or key in cand:
                    continue
                cand[key] = n
            time.sleep(1)
    for key, n in cand.items():
        loc = n.get("localization") or {}
        title, body = loc.get("title") or "", loc.get("body") or ""
        car = detect(title, body)
        if NOT_CAR_RE.search(norm(title)) or not any(wants_model(w, car) for w in cfg["waechter"] if w.get("on")):
            state["seen"][key] = time.time()
            continue
        lid = n["listingID"]
        props, coords = {}, None
        try:
            props, coords, body2 = tutti_detail(lid)
            body = body2 or body
        except Exception as e:  # Detailseite optional
            log("tutti Detail", lid, type(e).__name__)
        time.sleep(1)
        car = detect(title, body, props)
        pc = n.get("postcodeInformation") or {}
        src = (n.get("source") or {}).get("id") or "tutti"
        pro = bool(((n.get("sellerInfo") or {}).get("subscriptionInfo") or {}).get("subscriptionBadge"))
        l = dict(car, key=key, title=title, price=to_int(n.get("formattedPrice")),
                 plz=pc.get("postcode"), place=pc.get("locationName"),
                 lat=coords[0] if coords else None, lon=coords[1] if coords else None,
                 seller="Händler" if (src != "tutti" or pro) else "Privat",
                 source="tutti.ch" if src == "tutti" else "AutoScout24 via tutti",
                 url="https://www.tutti.ch/de/vi/%s" % lid, event="new")
        out.append(l)
    return out


# ---------------------------------------------------------------- Quelle: Suchabo-Mails
# Liest Suchabo-Mails (AutoScout24, tutti, Comparis, Ricardo …) aus einem eigenen
# Postfach-Ordner. Nur lesend (readonly), Mails bleiben unverändert.

PORTALE = {"autoscout24": "AutoScout24", "tutti": "tutti.ch", "comparis": "Comparis", "ricardo": "Ricardo",
           "car4you": "car4you", "anibis": "anibis", "autolina": "Autolina", "carforyou": "carforyou"}
LINK_SKIP_RE = re.compile(
    r"unsubscribe|abmeld|abbestell|austragen|einstellung|settings|preferences|profil|impressum|datenschutz|privacy"
    r"|apps\.apple|play\.google|facebook|instagram|linkedin|twitter|tiktok|youtube|mailto:|/hilfe|/help|kontakt"
    r"|suchabo|search-?alert|login|anmelden|\.(?:png|jpe?g|gif|webp)(?:\?|$)", re.I)
MONATE = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def html_text_links(h):
    """HTML in Text umwandeln; Links mit ihrer Position im Text merken."""
    import html as htmlmod
    h = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", h)
    parts, links, pos = [], [], 0
    for m in re.finditer(r"(?is)<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>|<br\s*/?>|</(?:p|div|td|tr|li|h\d|table)\s*>|<[^>]+>", h):
        t = htmlmod.unescape(h[pos:m.start()])
        t = re.sub(r"[ \t\r\f\v\xa0]+", " ", t)
        if t.strip():
            parts.append(t)
        n = sum(len(p) for p in parts)
        if m.group(1):
            links.append((n, htmlmod.unescape(m.group(1)).strip()))
        elif m.group(0)[:2].lower() in ("<b", "</"):
            parts.append("\n")
        pos = m.end()
    parts.append(htmlmod.unescape(h[pos:]))
    return "".join(parts), links


def plain_text_links(t):
    return t, [(m.start(), m.group(0)) for m in re.finditer(r"https?://[^\s<>\"']+", t)]


def parse_alert(body, portal, is_html=True):
    """Autos aus einer Suchabo-Mail holen. Anker ist der Preis: Jede Karte hat einen."""
    text, links = html_text_links(body) if is_html else plain_text_links(body)
    links = [(p, u) for p, u in links if u.startswith("http") and not LINK_SKIP_RE.search(u)]
    prices = [m for m in PRICE_RE.finditer(text) if 500 <= (to_int(m.group(1) or m.group(2)) or 0) <= 500000]
    out, prev_end = [], 0
    for i, pm in enumerate(prices):
        nxt = prices[i + 1].start() if i + 1 < len(prices) else len(text)
        lo = max(prev_end, pm.start() - 500)
        hi = min(nxt, pm.end() + 300)
        prev_end = pm.end()
        # Zuerst nur den Text bis zum Preis nehmen (Karte endet mit dem Preis), damit nichts
        # von der nächsten Karte hineinrutscht. Steht der Preis oben, den Text danach nehmen.
        seg = text[lo:pm.end()]
        car = detect(seg)
        if not car["brand"]:
            seg = text[pm.start():hi]
            car = detect(seg)
            lo = pm.start()
        if not car["brand"]:
            continue
        # Link: der letzte vor dem Preis im Block, sonst der erste danach
        before = [(p, u) for p, u in links if lo <= p <= pm.start()]
        after = [(p, u) for p, u in links if pm.end() <= p <= hi]
        link = (before[-1] if before else after[0] if after else (None, None))[1]
        if not link:
            continue
        # Titel: die Zeile mit der Marke
        title = next((ln.strip() for ln in seg.splitlines() if BRAND_RE.search(norm(ln))), seg[:80]).strip()
        pz = PLZ_RE.search(seg)
        out.append(dict(car, title=title[:120], seg=seg, price=to_int(pm.group(1) or pm.group(2)), url=link,
                        plz=pz.group(1) if pz else None, place=pz.group(2) if pz else None, source=portal))
    return out


def mail_listings(raw):
    import email
    import email.policy
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = str(msg.get("From", "")).lower()
    portal = next((name for dom, name in PORTALE.items() if dom in sender), "E-Mail")
    part = msg.get_body(preferencelist=("html", "plain"))
    if part is None:
        return []
    out = []
    for f in parse_alert(part.get_content(), portal, part.get_content_type() == "text/html"):
        f["key"] = "mail:%s|%s" % (portal, signature(f))
        f["source"] = portal + " (Suchabo)"
        f["seller"] = None
        f["event"] = "new"
        out.append(f)
    return out


_geo_cache = {}


def geocode_plz(plz, place):
    if not plz:
        return None, None
    k = "%s %s" % (plz, place or "")
    if k in _geo_cache:
        return _geo_cache[k]
    q = urllib.parse.urlencode({"type": "locations", "origins": "zipcode,gg25", "limit": 1, "sr": 4326, "searchText": k})
    res = (None, None)
    try:
        r = get_json("https://api3.geo.admin.ch/rest/services/api/SearchServer?" + q).get("results") or []
        if r:
            res = (r[0]["attrs"].get("lat"), r[0]["attrs"].get("lon"))
    except Exception:
        pass
    _geo_cache[k] = res
    time.sleep(0.2)
    return res


def source_mail(cfg, state):
    user = (os.environ.get("MAIL_USER") or "").strip()
    pw = re.sub(r"\s", "", os.environ.get("MAIL_APP_PASSWORD") or "")  # Gmail zeigt es in 4er-Gruppen an
    if not user or not pw:
        return []  # Mail-Brücke nicht eingerichtet
    import imaplib
    host = os.environ.get("MAIL_IMAP_HOST") or "imap.gmail.com"
    folder = os.environ.get("MAIL_ORDNER") or "Auto-Alarm"
    done = state["mails"]
    M = imaplib.IMAP4_SSL(host, timeout=30)
    out = []
    try:
        M.login(user, pw)
        typ, _ = M.select('"%s"' % folder, readonly=True)
        if typ != "OK":
            raise RuntimeError("Mail-Ordner \"%s\" nicht gefunden" % folder)
        d = datetime.fromtimestamp(time.time() - 3 * 86400, timezone.utc)
        since = "%02d-%s-%d" % (d.day, MONATE[d.month - 1], d.year)
        typ, data = M.uid("search", None, "SINCE", since)
        for uid in (data[0].split() if data and data[0] else [])[-80:]:
            k = "%s:%s" % (folder, uid.decode())
            if k in done:
                continue
            typ, msg = M.uid("fetch", uid, "(BODY.PEEK[])")
            raw = next((x[1] for x in msg if isinstance(x, tuple)), None)
            if raw:
                for l in mail_listings(raw):
                    old = state["cars"].get(l["key"])
                    if old:
                        if l.get("price") and old.get("price") and l["price"] < old["price"]:
                            out.append(dict(old, prev=old["price"], price=l["price"], event="drop"))
                    elif l["key"] not in state["seen"]:
                        out.append(l)
            done[k] = time.time()
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return out


SOURCES = [("tutti.ch", source_tutti), ("E-Mail", source_mail)]


def add_distance(cfg, items):
    h = cfg["home"]
    for l in items:
        if l.get("lat") is None and l.get("plz"):
            l["lat"], l["lon"] = geocode_plz(l["plz"], l.get("place"))
        l["dist"] = round(airline_km(h["lat"], h["lon"], l["lat"], l["lon"])) if l.get("lat") is not None else None


# ---------------------------------------------------------------- Push

PRIO = {"sofort": 4, "leise": 2, "abends": 3}


def ntfy(payload, dry):
    if dry:
        log("PUSH (nicht gesendet):", json.dumps(payload, ensure_ascii=False))
        return
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        sys.exit("NTFY_TOPIC fehlt.")
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    http(server, data=json.dumps(dict(payload, topic=topic)).encode("utf-8"), headers={"Content-Type": "application/json"})


def name(l):
    return re.sub(r"\s+", " ", l.get("title") or "%s" % l.get("brand")).strip()[:70]


def facts(l):
    p = []
    p.append(str(l["year"]) if l.get("year") else "Jg. ?")
    p.append("%s km" % chf(l["km"]) if l.get("km") is not None else "km ?")
    if l.get("fuel"):
        p.append(l["fuel"])
    if l.get("gear"):
        p.append(l["gear"])
    if l.get("place") or l.get("dist") is not None:
        p.append("%s%s" % (l.get("place") or "Ort ?", " (%d km)" % l["dist"] if l.get("dist") is not None else ""))
    if l.get("mfk"):
        p.append("ab MFK")
    return " · ".join(p)


def price_text(l):
    return "CHF %s" % chf(l["price"]) if l.get("price") else "Preis ?"


def tok(data):
    return base64.urlsafe_b64encode(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode().rstrip("=")


def page_url(file, data):
    base = os.environ.get("SEITE")
    if not base:
        return None
    return urllib.parse.urljoin(base.rstrip("/") + "/", file) + "#" + tok(data)


def anfrage_data(l):
    return {"n": name(l), "j": l.get("year"), "k": l.get("km"), "q": l.get("source"), "u": l["url"],
            "h": l.get("seller") == "Händler", "m": bool(l.get("mfk"))}


def push_listing(l, ws, dry):
    lvl = min((w["push"] for w in ws), key=lambda p: ["sofort", "leise", "abends"].index(p))
    if l.get("event") == "drop":
        title = "Preis gesenkt: %s, jetzt %s (−%s)" % (name(l), price_text(l), chf(l["prev"] - l["price"]))
    else:
        title = "Neu: %s, %s" % (name(l), price_text(l))
    msg = "%s\n%s · %s" % (facts(l), l["source"], " / ".join(w["name"] for w in ws))
    if l.get("unsure"):
        msg += "\nPrüfen: " + ", ".join(l["unsure"])
    actions = [{"action": "view", "label": "Inserat öffnen", "url": l["url"], "clear": True}]
    au = page_url("anfrage.html", anfrage_data(l))
    if au:
        actions.append({"action": "view", "label": "Anfragetext", "url": au})
    ntfy({"title": title, "message": msg, "click": l["url"], "actions": actions,
          "priority": PRIO[lvl], "tags": ["chart_with_downwards_trend" if l.get("event") == "drop" else "red_car"]}, dry)


def push_summary(title, items, dry, prio=3):
    if not items:
        return
    lines = ["%s, %s, %s" % (name(l), price_text(l), facts(l)) for l in items[:8]]
    if len(items) > 8:
        lines.append("… und %d weitere" % (len(items) - 8))
    lu = page_url("liste.html", {"t": title, "i": [dict(anfrage_data(l), p=price_text(l), f=facts(l),
                                                        d=l.get("event") == "drop") for l in items[:30]]})
    if lu:
        lines.append("Antippen zeigt alle Autos einzeln.")
    actions = ([{"action": "view", "label": "Alle ansehen", "url": lu}] if lu else []) + \
        [{"action": "view", "label": "Erstes Inserat", "url": items[0]["url"]}]
    ntfy({"title": title, "message": "\n".join(lines), "priority": prio, "tags": ["red_car"],
          "click": lu or items[0]["url"], "actions": actions}, dry)


def test_push(dry):
    l = {"title": "SKODA Octavia Combi 1.8 TSI 4x4 Style DSG (Test)", "price": 16400, "year": 2018, "km": 109000,
         "fuel": "Benzin", "gear": "Automat", "place": "Winterthur", "dist": 24, "source": "Test",
         "url": "https://www.tutti.ch/de/q/suche?query=octavia", "event": "new"}
    push_listing(l, [{"name": "Test", "push": "sofort"}], dry)


# ---------------------------------------------------------------- Ablauf

KEEP = ("key", "title", "brand", "model_text", "body", "awd", "fuel", "gear", "year", "km", "ps", "mfk", "price",
        "plz", "place", "lat", "lon", "dist", "seller", "source", "url")


def remember(state, l):
    state["cars"][l["key"]] = dict({k: l.get(k) for k in KEEP}, t=time.time())
    state["seen"][l["key"]] = time.time()


def show(l, ws):
    if PUBLIC_LOG:
        return
    why = "PASST " + "/".join(w["name"] for w in ws) if ws else "nein  "
    log("%s %-8s %-55s %-11s %s" % (why[:28].ljust(28), "SENKUNG" if l.get("event") == "drop" else "", name(l)[:55],
                                     price_text(l), facts(l)))


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    if "--test-push" in args:
        test_push(dry)
        log("Test-Push gesendet.")
        return
    cfg = load_config()
    if "--mail-datei" in args:
        items = []
        for p in args[args.index("--mail-datei") + 1:]:
            with open(p, "rb") as f:
                got = mail_listings(f.read())
            log("%s: %d Autos erkannt" % (os.path.basename(p), len(got)))
            items += got
        add_distance(cfg, items)
        for l in items:
            ws = matching(cfg, l)
            show(l, ws)
            if not ws:
                log("        Gründe:", "; ".join("%s: %s" % (w["name"], ", ".join(check(l, w)[0])) for w in cfg["waechter"]))
        return

    state = load_state()
    first = not state.get("initialized")
    now = datetime.now(TZ)
    n = cfg.get("nachtruhe") or {}
    night = bool(n.get("an")) and (now.hour >= n.get("von_uhr", 22) or now.hour < n.get("bis_uhr", 7))

    found = []
    for src, fn in SOURCES:
        try:
            items = fn(cfg, state)
            log("%s: %d neue oder geänderte Auto-Inserate" % (src, len(items)))
            found += items
            state["fails"][src] = 0
        except Exception as e:
            if getattr(e, "code", None) == 403:
                # Plattform sperrt diese Server (z. B. GitHub). Nicht umgehen, nicht warnen.
                log("%s: gesperrt (403), übersprungen" % src)
                continue
            c = state["fails"].get(src, 0) + 1
            state["fails"][src] = c
            log("%s: Fehler (%dx hintereinander): %s" % (src, c, e))
            if c == 12:  # rund 2 Stunden
                ntfy({"title": "Auto-Wächter: %s antwortet nicht" % src,
                      "message": "Seit etwa 2 Stunden klappt die Abfrage nicht: %s" % e, "priority": 3,
                      "tags": ["warning"]}, dry)

    add_distance(cfg, [l for l in found if l.get("event") == "new"])
    hits = []
    for l in found:
        ws = matching(cfg, l)
        if l.get("event") == "drop":
            ws = [w for w in ws if w.get("drops")]
        show(l, ws)
        if ws:
            hits.append((l, ws))

    if "--alle" in args:
        items = [l for l, _ in hits]
        push_summary("Aktuell %d passende Autos" % len(items), items, dry)
        log("Liste mit %d Autos gesendet." % len(items))
        return

    fresh = []
    for l, ws in hits:
        sig = signature(l) + ("|drop%s" % l["price"] if l.get("event") == "drop" else "")
        if sig in state["sigs"]:
            if not PUBLIC_LOG:
                log("Doppelt, kein Push:", name(l), l["source"])
            continue
        state["sigs"][sig] = time.time()
        fresh.append((l, ws))
    for l in found:
        remember(state, l)

    if first:
        items = [l for l, _ in fresh]
        if items:
            push_summary("Auto-Wächter läuft: %d passende Autos sind schon ausgeschrieben" % len(items), items, dry)
        else:
            ntfy({"title": "Auto-Wächter läuft", "message": "Gerade passt kein Auto. Ich melde mich, sobald eines kommt.",
                  "priority": 3, "tags": ["red_car"]}, dry)
        state["initialized"] = True
    else:
        now_push = []
        for l, ws in fresh:
            if all(w["push"] == "abends" for w in ws):
                state["evening"].append(l)
            elif night:
                state["queue"].append(l)
            else:
                now_push.append((l, ws))
        limit = cfg.get("max_einzel_pushes_pro_lauf", 8)
        for l, ws in now_push[:limit]:
            push_listing(l, ws, dry)
        if len(now_push) > limit:
            push_summary("%d weitere passende Autos" % (len(now_push) - limit), [l for l, _ in now_push[limit:]], dry)
        if not night and state["queue"]:
            push_summary("%d passende Autos aus der Nacht" % len(state["queue"]), state["queue"], dry)
            state["queue"] = []
        if now.hour >= 18 and state["evening"] and state.get("evening_day") != now.strftime("%Y-%m-%d"):
            push_summary("Sammel-Meldung: %d passende Autos von heute" % len(state["evening"]), state["evening"], dry)
            state["evening"] = []
            state["evening_day"] = now.strftime("%Y-%m-%d")

    state["last_run"] = datetime.now(timezone.utc).isoformat()
    if not dry:
        save_state(state)
    log("Fertig: %d Treffer, davon %d neu gemeldet." % (len(hits), len(fresh)))


if __name__ == "__main__":
    main()
