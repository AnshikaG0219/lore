"""Location gazetteer: turn what a post says into consistent city → zone → area and
place → state → country values (ADR-025).

Deliberately code, not AI: "Hauz Khas" must always be South Delhi. Extend these tables as new
places appear; facets can be rebuilt without any AI calls (scripts/rebuild_facets.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Delhi neighbourhoods → zone. Keys are lower-case aliases; values are (canonical area, zone).
_DELHI = {
    "South Delhi": [
        "hauz khas", "hauz khas village", "hkv", "green park", "saket", "select citywalk",
        "greater kailash", "gk 1", "gk 2", "gk-1", "gk-2", "gk i", "gk ii", "m block market",
        "defence colony", "lajpat nagar", "vasant vihar", "basant lok", "vasant kunj",
        "mehrauli", "qutub", "safdarjung", "humayunpur", "malviya nagar", "shahpur jat",
        "chhatarpur", "kalkaji", "nehru place", "south extension", "south ex", "kotla",
        "amar colony", "panchsheel", "chittaranjan park", "cr park", "munirka", "ina",
        "rk puram", "r.k. puram", "sangam vihar", "khanpur", "tughlakabad", "badarpur",
        "govindpuri", "sukhdev vihar", "new friends colony", "nfc", "maharani bagh",
        "hauz rani", "said ul ajaib", "saidulajab", "westend", "vasant enclave",
        "neb sarai", "pushp vihar", "sarvapriya vihar", "gulmohar park", "anand lok",
        "dilli haat", "sainik farm", "sarojini nagar", "lado sarai", "mahipalpur", "aerocity",
        "okhla", "jasola", "sarita vihar", "jangpura", "nizamuddin", "east of kailash",
        "moolchand", "andrews ganj", "masjid moth", "anupam", "sheikh sarai", "dhaula kuan",
    ],
    "North Delhi": [
        "majnu ka tila", "majnu-ka-tila", "kamla nagar", "civil lines", "north campus",
        "hudson lane", "gtb nagar", "mukherjee nagar", "model town", "vijay nagar",
        "shakti nagar", "timarpur", "pitampura", "rohini", "ashok vihar", "shalimar bagh",
        "netaji subhash place", "nsp", "kingsway camp", "burari", "azadpur",
        "prashant vihar", "rohini sector", "ashok vihar", "wazirpur", "jahangirpuri",
        "adarsh nagar", "narela", "bawana", "sant nagar", "outram lines",
        "delhi university", "du north campus", "hindu college", "khalsa college",
    ],
    "West Delhi": [
        "rajouri garden", "janakpuri", "tilak nagar", "punjabi bagh", "dwarka", "subhash nagar",
        "vikaspuri", "uttam nagar", "patel nagar", "karol bagh", "kirti nagar", "moti nagar",
        "paschim vihar", "tagore garden", "ramesh nagar", "naraina", "rajinder nagar",
        "hari nagar", "meera bagh", "vikas puri", "palam", "najafgarh", "mundka",
        "nangloi", "peeragarhi", "shastri nagar", "inderlok", "west patel nagar",
    ],
    "East Delhi": [
        "laxmi nagar", "preet vihar", "mayur vihar", "shahdara", "patparganj", "nirman vihar",
        "anand vihar", "krishna nagar", "geeta colony", "akshardham", "ip extension",
        "vivek vihar", "karkardooma", "pandav nagar", "shakarpur", "ganesh nagar",
        "vinod nagar", "mandawali", "dilshad garden", "yamuna vihar", "gandhi nagar",
        "jheel", "seelampur", "khichripur", "kalyanpuri",
    ],
    "Central / New Delhi": [
        "connaught place", "cp", "janpath", "khan market", "lodhi colony", "lodhi garden",
        "lodhi road", "mandi house", "chanakyapuri", "meherchand market", "sunder nagar",
        "india gate", "golf links", "jor bagh", "bengali market", "gole market",
        "shangri-la", "pragati maidan", "bharat mandapam", "barakhamba", "kartavya path",
        "rashtrapati bhavan", "lutyens",
    ],
    "Old Delhi": [
        "chandni chowk", "old delhi", "jama masjid", "daryaganj", "paharganj", "khari baoli",
        "red fort", "lal qila", "chawri bazar", "ballimaran", "sadar bazar", "kashmere gate",
    ],
}

# NCR cities: aliases for the city itself and localities inside it.
_NCR = {
    "Noida": ["noida", "botanical garden noida", "dlf mall of india", "gip mall",
              "film city noida", "atta market", "brahmaputra market"],
    "Gurugram": ["gurugram", "gurgaon", "cyber hub", "cyberhub", "golf course road",
                 "sohna road", "mg road gurgaon", "sector 29 gurgaon", "udyog vihar",
                 "dlf phase", "galleria market", "ambience mall gurgaon"],
    "Ghaziabad": ["ghaziabad", "indirapuram", "vaishali", "kaushambi", "raj nagar",
                  "raj nagar extension", "crossings republik", "vasundhara", "shipra"],
    "Faridabad": ["faridabad", "surajkund", "ballabgarh"],
    "Sonipat": ["sonipat", "sonepat", "kundli", "murthal"],
    "Greater Noida": ["greater noida", "knowledge park", "alpha 1", "pari chowk",
                      "greater noida west", "noida extension", "gaur city"],
}

# Destinations → Indian state / UT.
_STATES = {
    "Ladakh": ["ladakh", "leh", "nubra", "pangong", "zanskar", "kargil", "khardung la",
               "tso moriri", "hanle", "turtuk", "diskit", "hunder", "panamik", "durbuk"],
    "Himachal Pradesh": ["himachal", "manali", "kasol", "spiti", "shimla", "dharamshala",
                         "mcleodganj", "mcleod ganj", "bir", "billing", "tirthan", "jibhi",
                         "kasauli", "kullu", "kheerganga", "kalga", "tosh", "chitkul",
                         "sangla", "kaza", "dalhousie", "khajjiar", "chamba", "barot",
                         "parvati valley", "triund", "hampta", "rohtang", "sissu", "kinnaur"],
    "Uttarakhand": ["uttarakhand", "rishikesh", "mussoorie", "nainital", "kedarnath",
                    "badrinath", "auli", "chopta", "tungnath", "jim corbett", "corbett",
                    "dehradun", "haridwar", "lansdowne", "kasar devi", "almora", "munsiyari",
                    "valley of flowers", "kedarkantha", "har ki dun", "dayara bugyal",
                    "landour", "binsar", "bhimtal", "mukteshwar"],
    "Jammu & Kashmir": ["kashmir", "gulmarg", "pahalgam", "srinagar", "sonamarg", "gurez",
                        "dal lake", "jammu", "keran", "doodhpathri", "vaishno devi"],
    "Rajasthan": ["rajasthan", "jaipur", "udaipur", "jaisalmer", "jodhpur", "pushkar",
                  "mount abu", "bikaner", "ranthambore", "chittorgarh", "ajmer"],
    "Goa": ["goa", "panaji", "anjuna", "palolem", "vagator", "calangute", "baga", "arambol"],
    "Uttar Pradesh": ["uttar pradesh", "varanasi", "banaras", "benaras", "kashi", "lucknow",
                      "agra", "taj mahal", "mathura", "vrindavan", "ayodhya", "prayagraj",
                      "allahabad"],
    "Kerala": ["kerala", "munnar", "alleppey", "alappuzha", "kochi", "cochin", "varkala",
               "wayanad", "kovalam", "thekkady"],
    "Karnataka": ["karnataka", "bangalore", "bengaluru", "coorg", "hampi", "gokarna",
                  "chikmagalur", "mysore", "mysuru"],
    "Maharashtra": ["maharashtra", "mumbai", "pune", "lonavala", "alibaug", "nashik",
                    "mahabaleshwar"],
    "Tamil Nadu": ["tamil nadu", "chennai", "ooty", "kodaikanal", "madurai", "rameswaram"],
    "Puducherry": ["pondicherry", "puducherry", "auroville"],
    "Meghalaya": ["meghalaya", "shillong", "cherrapunji", "sohra", "dawki", "mawlynnong"],
    "Sikkim": ["sikkim", "gangtok", "pelling", "lachung", "yumthang", "gurudongmar"],
    "West Bengal": ["west bengal", "kolkata", "calcutta", "darjeeling", "kalimpong"],
    "Arunachal Pradesh": ["arunachal", "tawang", "ziro"],
    "Andaman & Nicobar": ["andaman", "havelock", "neil island", "port blair"],
    "Punjab": ["punjab", "amritsar", "golden temple", "chandigarh"],
    "Delhi": ["delhi", "new delhi"],
    "Haryana": ["haryana", "gurugram", "gurgaon", "faridabad"],
    "Gujarat": ["gujarat", "ahmedabad", "rann of kutch", "kutch", "dwarka gujarat"],
    "Madhya Pradesh": ["madhya pradesh", "khajuraho", "pachmarhi", "orchha", "indore",
                       "bhopal", "kanha"],
    "Odisha": ["odisha", "puri", "konark", "bhubaneswar"],
    "Assam": ["assam", "guwahati", "kaziranga", "majuli"],
}

_COUNTRIES = {
    "Japan": ["japan", "tokyo", "kyoto", "osaka"], "Thailand": ["thailand", "bangkok", "phuket"],
    "Indonesia": ["indonesia", "bali"], "Vietnam": ["vietnam", "hanoi", "ho chi minh"],
    "Sri Lanka": ["sri lanka", "colombo", "ella"], "Nepal": ["nepal", "kathmandu", "pokhara"],
    "Bhutan": ["bhutan", "thimphu", "paro"], "UAE": ["dubai", "abu dhabi", "uae"],
    "Maldives": ["maldives"], "Singapore": ["singapore"], "South Korea": ["korea", "seoul"],
    "France": ["france", "paris"], "Italy": ["italy", "rome", "venice", "florence", "amalfi"],
    "United Kingdom": ["london", "uk", "england", "scotland", "united kingdom"],
    "United States": ["usa", "united states", "new york", "california"],
    "Switzerland": ["switzerland", "zurich", "interlaken"], "Turkey": ["turkey", "istanbul",
                                                                      "cappadocia"],
    "Malaysia": ["malaysia", "kuala lumpur"], "Georgia": ["georgia", "tbilisi"],
    "Kazakhstan": ["kazakhstan", "almaty"], "Azerbaijan": ["azerbaijan", "baku"],
    "Egypt": ["egypt", "cairo"], "Spain": ["spain", "barcelona", "madrid"],
    "Greece": ["greece", "santorini", "athens"], "Australia": ["australia", "sydney"],
    "Philippines": ["philippines"], "Mauritius": ["mauritius"], "Hong Kong": ["hong kong"],
}


def _index(table: dict[str, list[str]]) -> list[tuple[re.Pattern, str]]:
    pairs = [(alias, key) for key, aliases in table.items() for alias in aliases]
    pairs.sort(key=lambda p: -len(p[0]))  # longest alias first ("greater noida" before "noida")
    return [(re.compile(rf"(?<![a-z]){re.escape(a)}(?![a-z])"), key) for a, key in pairs]


_DELHI_IDX = [(p, zone, alias) for zone, aliases in _DELHI.items() for alias in aliases
              for p in [re.compile(rf"(?<![a-z]){re.escape(alias)}(?![a-z])")]]
_DELHI_IDX.sort(key=lambda t: -len(t[2]))
_NCR_IDX = _index(_NCR)
_STATE_IDX = _index(_STATES)
_COUNTRY_IDX = _index(_COUNTRIES)
_SECTOR = re.compile(r"\b(?:sector|sec\.?)[\s-]*(\d{1,3}[a-z]?)\b")


@dataclass(frozen=True, slots=True)
class Place:
    country: str | None = None
    state: str | None = None
    city: str | None = None
    zone: str | None = None   # Delhi only: South / North / West / East / Central / Old
    area: str | None = None


def _first(idx, text: str) -> str | None:
    for pattern, key in idx:
        if pattern.search(text):
            return key
    return None


def resolve(*parts: str | None) -> Place:
    """Resolve free-text location fields (area, city, state, place name…) to a Place."""
    text = " , ".join(p.lower() for p in parts if p).replace("’", "'")
    if not text.strip():
        return Place()

    # Delhi neighbourhoods first: most specific signal.
    for pattern, zone, alias in _DELHI_IDX:
        if pattern.search(text):
            return Place("India", "Delhi", "Delhi", zone, alias.title())

    city = _first(_NCR_IDX, text)
    if city:
        sector = _SECTOR.search(text)
        area = f"Sector {sector.group(1).upper()}" if sector else None
        if area is None:
            for pattern, key in _NCR_IDX:
                m = pattern.search(text)
                if key == city and m and m.group().lower() not in (city.lower(), "gurgaon"):
                    area = m.group().title()
                    break
        state = "Uttar Pradesh" if city in ("Noida", "Greater Noida", "Ghaziabad") else "Haryana"
        return Place("India", state, city, None, area)

    state = _first(_STATE_IDX, text)
    if state == "Delhi":
        return Place("India", "Delhi", "Delhi", None, None)
    if state:
        return Place("India", state, None, None, None)

    country = _first(_COUNTRY_IDX, text)
    if country:
        return Place(country, None, None, None, None)
    if re.search(r"(?<![a-z])india(?![a-z])", text):
        return Place("India")
    return Place()
