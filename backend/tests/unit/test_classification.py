import pytest

from app.processing.classification.facets import build_facets
from app.processing.classification.gazetteer import Place, resolve
from app.processing.classification.schema import Classification


@pytest.mark.parametrize(("parts", "expected"), [
    (("Hauz Khas Village",), Place("India", "Delhi", "Delhi", "South Delhi", "Hauz Khas Village")),
    (("Basant Lok", "Delhi"), Place("India", "Delhi", "Delhi", "South Delhi", "Basant Lok")),
    (("Majnu ka Tila",), Place("India", "Delhi", "Delhi", "North Delhi", "Majnu Ka Tila")),
    (("Khan Market",), Place("India", "Delhi", "Delhi", "Central / New Delhi", "Khan Market")),
    (("Chandni Chowk", "New Delhi"), Place("India", "Delhi", "Delhi", "Old Delhi",
                                           "Chandni Chowk")),
    (("Rajouri Garden",), Place("India", "Delhi", "Delhi", "West Delhi", "Rajouri Garden")),
    (("Sector 18", "Noida"), Place("India", "Uttar Pradesh", "Noida", None, "Sector 18")),
    (("Greater Noida West",), Place("India", "Uttar Pradesh", "Noida", None, "Greater Noida West")),
    (("Indirapuram",), Place("India", "Uttar Pradesh", "Ghaziabad", None, "Indirapuram")),
    (("CyberHub", "Gurgaon"), Place("India", "Haryana", "Gurugram", None, "Cyberhub")),
    (("Delhi",), Place("India", "Delhi", "Delhi", None, None)),
    (("Panamik", "Nubra Valley"), Place("India", "Ladakh")),
    (("Kasol",), Place("India", "Himachal Pradesh")),
    (("Kedarkantha trek",), Place("India", "Uttarakhand")),
    (("Varanasi",), Place("India", "Uttar Pradesh")),
    (("Kyoto",), Place("Japan")),
    (("Bali", "Indonesia"), Place("Indonesia")),
    ((None, ""), Place()),
    (("Somewhere nice",), Place()),
])
def test_resolve(parts, expected):
    assert resolve(*parts) == expected


def test_resolve_word_boundaries():
    # "cp" (Connaught Place) must not match inside other words; "china" ≠ "ina".
    assert resolve("Chinese place in Noida").city == "Noida"
    assert resolve("cpu benchmark") == Place()


def test_choice_fields_are_forgiving():
    c = Classification.model_validate({
        "title": "t", "summary": "s", "post_type": "Eat & Drink", "topic": "Food Drinks",
        "information_sufficient": True, "confidence": 0.7,
        "eat_drink": {"venues": [{"name": "X", "venue_type": "Café", "cuisines": ["Italian",
                                  "Neapolitan pizza", "italian"], "evidence": "EXPLICIT"}]},
    })
    v = c.eat_drink.venues[0]
    assert (c.post_type, c.topic) == ("eat_drink", "food_drinks")  # "Eat & Drink" normalised
    assert v.venue_type == "cafe"  # accent stripped
    assert v.cuisines == ["italian", "other"] and v.evidence == "explicit"


def test_facets_for_a_delhi_cafe():
    c = {"post_type": "eat_drink", "topic": "food_drinks", "information_sufficient": True,
         "eat_drink": {"venues": [{"name": "Kunzum", "venue_type": "cafe",
                                   "cuisines": ["coffee"], "good_for": ["work_study"],
                                   "vibe": ["hidden_gem"], "price_level": "budget",
                                   "location": {"area": "Hauz Khas Village"}}]}}
    f = dict.fromkeys(build_facets(c, origin="saved"))
    for row in [("type", "eat_drink"), ("venue_type", "cafe"), ("cuisine", "coffee"),
                ("venue.zone", "South Delhi"), ("zone", "South Delhi"), ("city", "Delhi"),
                ("venue.area", "Hauz Khas Village"), ("good_for", "work_study"),
                ("origin", "saved"), ("price_level", "budget")]:
        assert row in f, row


def test_facets_for_a_trek():
    c = {"post_type": "trip", "topic": "travel", "information_sufficient": True,
         "trip": {"destinations": [{"place": "Kedarkantha"}], "trip_types": ["mountains"],
                  "trek": {"name": "Kedarkantha", "difficulty": "moderate"},
                  "terrain": ["snow"]}}
    f = set(build_facets(c))
    assert {("trip_type", "trek"), ("trip_type", "mountains"), ("trek_difficulty", "moderate"),
            ("trip.state", "Uttarakhand"), ("state", "Uttarakhand"), ("trip_scope", "india"),
            ("terrain", "snow")} <= f


def test_facets_for_international_trip_and_unclear_post():
    c = {"post_type": "trip", "topic": "travel", "information_sufficient": False,
         "trip": {"destinations": [{"city": "Kyoto"}]}}
    f = set(build_facets(c))
    assert ("trip_scope", "international") in f and ("country", "Japan") in f
    assert ("quality", "unclear") in f


def test_fun_posts_get_minimal_facets():
    f = set(build_facets({"post_type": "fun", "topic": "humour", "fun": {"humour_type": "meme"},
                          "information_sufficient": True}, origin="shared"))
    assert f == {("type", "fun"), ("topic", "humour"), ("humour", "meme"), ("origin", "shared")}


def test_unknown_values_map_to_other():
    c = Classification.model_validate({"title": "t", "summary": "s", "post_type": "astrology",
                                       "topic": "zodiac", "information_sufficient": True,
                                       "confidence": 0.5})
    assert (c.post_type, c.topic) == ("other", "other")
