"""Stage 1 (photos): candidate pool from the Unsplash Lite dataset -> work/pool.jsonl.

    python3 -I demo/corpus/devices/photo_pool.py

Input (untrusted, read as data only): the official Unsplash Lite download
``$HPIS_DEVICES_BUILD/downloads/unsplash-official/lite.zip`` (photos.tsv000 and
keywords.tsv000). The Hugging Face mirror ``1aurent/unsplash-lite`` holds the same
25,000 photos, but its location columns are empty ("nan"), so the location
metadata comes from the official file. Dataset metadata is used only to choose
candidates; none of its text is copied into the repository.

Candidates: photos located in the target countries plus location-less photos of
generic scenes (food, cafés, parks, skylines, beaches, seasons …) without people.
The vision model later decides what each photo shows and whether it fits.
"""

from __future__ import annotations

import collections
import csv
import io
import json
import math
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.corpus.devices.common import DOWNLOADS, WORK  # noqa: E402

csv.field_size_limit(10**8)

COUNTRY = {
    "germany": "德国", "deutschland": "德国", "bavaria, germany": "德国", "allemagne": "德国",
    "south korea": "韩国", "korea": "韩国", "대한민국": "韩国", "republic of korea": "韩国",
    "japan": "日本", "日本": "日本", "japon": "日本",
    "china": "中国", "中国": "中国", "中國": "中国",
}
# canonical city by substring of (location name + city), checked in order
CITY = [
    (r"münchen|munich|muenchen", "慕尼黑"), (r"frankfurt", "法兰克福"), (r"berlin|potsdam", "柏林"),
    (r"seoul|서울|incheon|인천|bundang", "首尔"), (r"busan|부산", "釜山"),
    (r"tokyo|東京|sumida|shinjuku|shibuya|koto city|toyosu|ginza|asakusa|odaiba|taitō|台東|ueno", "东京"),
    (r"kyoto|kyōto|京都|fushimi|daigo", "京都"), (r"nara|奈良", "奈良"),
    (r"beijing|北京|peking|chaoyang|ba da ling", "北京"), (r"shanghai|上海", "上海"),
    (r"hangzhou|杭州", "杭州"), (r"suzhou|苏州|wuxi|无锡", "苏州"), (r"yangzhou|扬州", "扬州"),
    (r"shenzhen|深圳", "深圳"), (r"sanya|三亚|hainan|海南", "三亚"), (r"tianjin|天津", "天津"),
    (r"qinhuangdao|beidaihe|北戴河", "秦皇岛"),
]
PEOPLE = re.compile(r"\b(man|men|woman|women|person|people|persons|girl|girls|boy|boys|child|children|kid|kids|"
                    r"couple|crowd|human|face|portrait|selfie|bride|groom|model|lady|guy|baby|family|"
                    r"someone|tourist|tourists|hiker|runner|crowds|friends|player|players)\b")
ANIMAL = re.compile(r"\b(dog|dogs|cat|cats|kitten|puppy|bird|birds|eagle|owl|bear|monkey|macaque|panda|deer|fish|"
                    r"jellyfish|koi|goldfish|horse|cow|sheep|lamb|insect|bee|butterfly|spider|squirrel|duck|swan|"
                    r"gull|seagull|lion|tiger|elephant|zebra|giraffe|fox|wolf|rabbit|alligator)\b")
CATS = {
    "food": r"\b(food|dish|meal|noodle|noodles|dumpling|dumplings|sushi|ramen|soup|cuisine|breakfast|dessert|cake|"
            r"bread|pastry|rice|barbecue|bbq|hot pot|salad|fruit|tea|sausage|pretzel|beer|seafood|bowl of|plate of)\b",
    "coffee": r"\b(coffee|latte|cappuccino|espresso|cafe|café|coffee shop)\b",
    "night": r"\b(night|nighttime|neon|city lights|night sky)\b",
    "city": r"\b(city|cityscape|skyline|skyscraper|skyscrapers|downtown|urban|high rise|high-rise|buildings)\b",
    "street": r"\b(street|alley|road|lane|old town|town|building|architecture|house|houses)\b",
    "beach": r"\b(beach|seashore|shore|coast|palm tree|palm trees|ocean|sea|island|tropical)\b",
    "park": r"\b(park|garden|pond|lake|trees|forest|path|pathway)\b",
    "mountain": r"\b(mountain|mountains|hill|hills|hiking|valley|peak|cliff)\b",
    "sakura": r"\b(cherry blossom|cherry blossoms|sakura)\b",
    "flower": r"\b(flower|flowers|blossom|blossoms|bloom|tulip|tulips|rose|roses|peony|lotus)\b",
    "autumn": r"\b(autumn|fall|foliage|maple|ginkgo|yellow leaves|red leaves|orange leaves)\b",
    "snow": r"\b(snow|snowy|winter|ice|frozen)\b",
    "sunset": r"\b(sunset|sunrise|dusk|dawn|golden hour)\b",
    "plane": r"\b(airplane|aircraft|aeroplane|plane|airport|airplane wing|flight)\b",
    "train": r"\b(train|railway|subway|metro|station|tram)\b",
    "hotel": r"\b(hotel|bedroom|bed|room)\b",
    "work": r"\b(conference|auditorium|meeting|presentation|exhibition|seminar|convention|laptop|office|desk|"
            r"workspace|whiteboard|computer)\b",
    "running": r"\b(running|jogging|track|marathon|sneakers|running shoes|trail)\b",
    "temple": r"\b(temple|shrine|pagoda|torii|buddha|palace|lantern|lanterns|great wall)\b",
    "river": r"\b(river|bridge|canal|waterfront|harbor|harbour|port)\b",
    "bike": r"\b(bicycle|bike|cycling)\b",
    "pool": r"\b(swimming pool|pool)\b",
    "newyear": r"\b(chinese new year|lantern|lanterns|firework|fireworks|red envelope|dumplings)\b",
    "museum": r"\b(museum|exhibit|gallery|aquarium)\b",
    "home": r"\b(living room|kitchen|window|bookshelf|books|book|plant|plants|houseplant|toy|toys|lego|kite)\b",
}
# location-less candidates per category, best-downloaded first
QUOTA = {"food": 340, "coffee": 60, "night": 170, "city": 150, "street": 150, "beach": 100, "park": 190, "mountain": 25,
         "sakura": 160, "flower": 130, "autumn": 120, "snow": 100, "sunset": 90, "plane": 60, "train": 40, "hotel": 35,
         "work": 90, "running": 40, "temple": 30, "river": 80, "bike": 20, "pool": 20, "newyear": 30, "museum": 15,
         "home": 130}
EXCLUDE = re.compile(r"\b(new york|nyc|paris|london|eiffel|statue of liberty|golden gate|san francisco|chicago|"
                     r"toronto|vancouver|sydney|dubai|venice|rome|barcelona|amsterdam|prague|lisbon|istanbul|"
                     r"hong kong|singapore|bangkok|taipei|india|mexico|cuba|morocco|egypt|bali|thailand|vietnam|"
                     r"santorini|iceland|hawaii|california|texas|florida|yosemite|grand canyon|times square|"
                     r"christmas|halloween|thanksgiving|church|cathedral|mosque|cross|graffiti|protest|weapon|gun|"
                     r"cigarette|smoking|alcohol|wine|cocktail|whisky|nude|bikini|underwear|tattoo|blood|skull)\b")


def _f(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def main() -> None:
    z = zipfile.ZipFile(DOWNLOADS / "unsplash-official" / "lite.zip")
    with z.open("photos.tsv000") as fh:
        photos = list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE))
    kws: dict[str, list] = collections.defaultdict(list)
    with z.open("keywords.tsv000") as fh:
        for x in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE):
            c = [v for v in (_f(x["ai_service_1_confidence"]), _f(x["ai_service_2_confidence"])) if v is not None]
            user = x["suggested_by_user"] == "t"
            conf = max(c) if c else (70.0 if user else 0.0)
            if conf >= 80 or user:
                kws[x["photo_id"]].append((x["keyword"].lower(), round(conf, 1), user))
    out = {}
    per_cat = collections.Counter()
    ranked = sorted(photos, key=lambda p: -(int(p["stats_downloads"] or 0)))
    for p in ranked:
        pid = p["photo_id"]
        w, h = int(p["photo_width"] or 0), int(p["photo_height"] or 0)
        if min(w, h) < 1100 or not (0.62 <= w / max(h, 1) <= 1.85):
            continue
        ai = (p["ai_description"] or "").lower()
        desc = (p["photo_description"] or "").lower()
        strong = " ".join(k for k, c, u in kws.get(pid, []) if c >= 88)
        user_kw = " ".join(k for k, c, u in kws.get(pid, []) if u)
        text = f"{ai} | {strong}"
        country = COUNTRY.get((p["photo_location_country"] or "").strip().lower())
        locstr = " ".join([p["photo_location_name"], p["photo_location_city"], p["ai_primary_landmark_name"]]).lower()
        city = None
        for pat, name in CITY:
            if re.search(pat, locstr):
                city = name
                break
        if country is None and city in ("首尔", "釜山"):
            country = "韩国"
        people = bool(PEOPLE.search(ai))
        animal = bool(ANIMAL.search(ai))
        cats = [k for k, pat in CATS.items() if re.search(pat, text)]
        located_elsewhere = bool((p["photo_location_country"] or "").strip()) and country is None
        rec = {
            "id": pid, "url": p["photo_url"], "image_url": p["photo_image_url"], "w": w, "h": h,
            "user": p["photographer_username"],
            "name": " ".join(x for x in (p["photographer_first_name"], p["photographer_last_name"]) if x).strip(),
            "country": country, "city": city,
            "loc": {"name": p["photo_location_name"], "city": p["photo_location_city"],
                    "country": p["photo_location_country"], "lat": _f(p["photo_location_latitude"]),
                    "lon": _f(p["photo_location_longitude"])},
            "landmark": p["ai_primary_landmark_name"], "ai": p["ai_description"], "cats": cats,
            "people": people, "animal": animal, "downloads": int(p["stats_downloads"] or 0),
        }
        if country in ("德国", "韩国", "日本", "中国") or city:
            if country == "德国" and city is None and not set(cats) & {"city", "street", "night", "food", "coffee",
                                                                        "river", "train", "plane", "work"}:
                continue  # rural Germany: not on this trip
            if country == "中国" and city is None:
                continue  # elsewhere in China (Tibet, Xinjiang …): not a place 林舟 visited
            rec["why"] = "located"
            out[pid] = rec
            continue
        if located_elsewhere or people or animal or EXCLUDE.search(text + " " + desc + " " + user_kw):
            continue
        for c in cats:
            if per_cat[c] < QUOTA.get(c, 0):
                per_cat[c] += 1
                rec["why"] = f"generic:{c}"
                out.setdefault(pid, rec)
                break
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / "pool.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for rec in out.values():
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    by_why = collections.Counter(r["why"].split(":")[0] for r in out.values())
    by_city = collections.Counter(r["city"] or r["country"] for r in out.values() if r["why"] == "located")
    print(f"[pool] {len(out)} candidates -> {path}", dict(by_why))
    print("[pool] located:", dict(by_city))
    print("[pool] generic:", dict(per_cat))


if __name__ == "__main__":
    main()
