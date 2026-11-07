import pytest

from app.ingestion.url_parser import UrlKind, extract_urls, parse_url


@pytest.mark.parametrize(
    ("raw", "kind", "shortcode", "canonical"),
    [
        ("https://www.instagram.com/p/C2xYz_12345/", UrlKind.POST, "C2xYz_12345",
         "https://www.instagram.com/p/C2xYz_12345/"),
        ("https://instagram.com/reel/C1aBcDeFgHi?utm_source=ig_web_copy_link", UrlKind.REEL,
         "C1aBcDeFgHi", "https://www.instagram.com/reel/C1aBcDeFgHi/"),
        ("https://www.instagram.com/reels/C1aBcDeFgHi/?igsh=abc", UrlKind.REEL, "C1aBcDeFgHi",
         "https://www.instagram.com/reel/C1aBcDeFgHi/"),
        ("http://m.instagram.com/tv/B9xx-yyzz/", UrlKind.TV, "B9xx-yyzz",
         "https://www.instagram.com/tv/B9xx-yyzz/"),
        ("instagr.am/p/ShortLnk1/", UrlKind.POST, "ShortLnk1",
         "https://www.instagram.com/p/ShortLnk1/"),
        ("www.instagram.com/someuser/reel/C1aBcDeFgHi/", UrlKind.REEL, "C1aBcDeFgHi",
         "https://www.instagram.com/reel/C1aBcDeFgHi/"),
        ("https://www.instagram.com/p/C2xYz_12345/#comments", UrlKind.POST, "C2xYz_12345",
         "https://www.instagram.com/p/C2xYz_12345/"),
        ("https://www.instagram.com/p/C2xYz_12345/.", UrlKind.POST, "C2xYz_12345",
         "https://www.instagram.com/p/C2xYz_12345/"),
    ],
)
def test_media_urls_are_canonicalised(raw, kind, shortcode, canonical):
    parsed = parse_url(raw)
    assert parsed is not None
    assert parsed.kind is kind
    assert parsed.shortcode == shortcode
    assert parsed.canonical_url == canonical
    assert parsed.identity_key == f"instagram:shortcode:{shortcode}"


def test_shortcodes_are_case_sensitive():
    a, b = parse_url("instagram.com/p/CaseSens01/"), parse_url("instagram.com/p/casesens01/")
    assert a.identity_key != b.identity_key


def test_post_and_reel_with_same_shortcode_share_identity():
    assert (parse_url("instagram.com/p/C1aBcDeFgHi/").identity_key
            == parse_url("instagram.com/reel/C1aBcDeFgHi/").identity_key)


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("https://www.instagram.com/share/reel/BAbcdEfgh1/", UrlKind.SHARE_LINK),
        ("https://www.instagram.com/s/aGlnaGxpZ2h0OjE3/", UrlKind.SHARE_LINK),
        ("https://www.instagram.com/stories/someone/3312345678901234567/", UrlKind.STORY),
        ("https://www.instagram.com/Mountain_Cafes/", UrlKind.PROFILE),
        ("https://www.instagram.com/_u/mountain_cafes", UrlKind.PROFILE),
        ("https://www.instagram.com/explore/tags/ladakh/", UrlKind.OTHER),
    ],
)
def test_non_media_urls_are_classified_not_merged(raw, kind):
    parsed = parse_url(raw)
    assert parsed.kind is kind
    assert parsed.shortcode is None
    assert parsed.identity_key.startswith("instagram:url:")


@pytest.mark.parametrize(
    "raw",
    ["https://example.com/p/C2xYz_12345/", "https://notinstagram.com/p/C2xYz_12345/",
     "https://instagram.com.evil.test/p/C2xYz_12345/", "", "not a url",
     "https://www.instagram.com/"],
)
def test_rejects_non_instagram(raw):
    assert parse_url(raw) is None


def test_rejects_invalid_shortcode():
    assert parse_url("https://www.instagram.com/p/ab/") is None
    assert parse_url("https://www.instagram.com/p/bad$code!/") is None


def test_extract_urls_from_text_dedupes_and_keeps_order():
    text = (
        "see https://www.instagram.com/reel/C1aBcDeFgHi/?igsh=x, and "
        "instagram.com/p/C1aBcDeFgHi again (also https://www.instagram.com/p/C2xYz_12345/)."
    )
    urls = extract_urls(text)
    assert [u.shortcode for u in urls] == ["C1aBcDeFgHi", "C2xYz_12345"]
