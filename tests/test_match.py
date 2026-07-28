from datetime import datetime, timezone
from radar.item import Item
from radar.match import (term_hits, haystack, stack_matches, category_matches,
                         score_importance, exclusion_hit)

NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)


def _item(**kw):
    base = dict(id="i", title="t", url="u", source_type="rss", category="backend",
                published=NOW, summary="s")
    base.update(kw)
    return Item(**base)


# --- word-boundary matching --------------------------------------------------

def test_rails_does_not_match_guardrails():
    it = _item(title="Agentic AI Needs Guardrails, Not Guesswork",
               url="https://www.docker.com/blog/agentic-ai-needs-guardrails/")
    assert stack_matches(it, {"frameworks": ["rails"]}) == []


def test_rails_matches_rails_with_trailing_punctuation():
    it = _item(title="Happy Anniversary Rails!", summary="", url="u")
    assert stack_matches(it, {"frameworks": ["rails"]}) == ["rails"]


def test_next_js_term_matches_despite_dot():
    it = _item(title="next.js 16 released", summary="", url="u")
    assert stack_matches(it, {"packages": ["next.js"]}) == ["next.js"]


def test_react_dom_term_matches_despite_hyphen():
    it = _item(title="react-dom patch", summary="", url="u")
    assert stack_matches(it, {"packages": ["react-dom"]}) == ["react-dom"]


def test_short_term_does_not_match_inside_longer_word():
    # the footgun documented in config/radar.example.toml: substring matching
    # made a bare "ai" keyword unusable because it hit "available"/"email".
    it = _item(category="ai", title="Now available by email", summary="", url="u")
    assert category_matches(it, {"ai": ["ai"]}) == []


def test_term_listed_in_two_stack_lists_is_reported_once():
    # `rails` sits in both frameworks and packages in the shipped config; the
    # concatenated list must not report it twice.
    it = _item(title="Rails 8 released", summary="", url="u")
    stack = {"packages": ["rails"], "frameworks": ["rails"], "languages": ["ruby"]}
    assert stack_matches(it, stack) == ["rails"]


def test_matching_is_case_insensitive():
    assert term_hits(["Rails"], "rails 8") == ["Rails"]
    assert term_hits(["rails"], "RAILS 8") == ["rails"]


def test_haystack_spans_title_summary_and_url():
    it = _item(title="A", summary="B", url="C")
    assert haystack(it) == "A B C"


# --- scoring (moved from tests/test_fetch.py) --------------------------------

def test_score_importance_precedence():
    assert score_importance(_item(severity="critical"), {}) == "critical"
    assert score_importance(_item(title="rails x"), {"packages": ["rails"]}) == "high"
    assert score_importance(_item(source_type="rss"), {}) == "low"
    assert score_importance(_item(source_type="github"), {}) == "medium"


def test_category_keywords_boost_matching_item_to_high():
    kw = {"ai": ["llm", "claude"]}
    assert score_importance(_item(category="ai", title="New Claude 5 model"), {}, kw) == "high"


def test_category_keywords_do_not_boost_non_matching_item():
    kw = {"ai": ["llm", "claude"]}
    assert score_importance(_item(category="ai", title="unrelated musings"), {}, kw) == "low"


def test_category_keywords_are_scoped_to_their_category():
    kw = {"ai": ["claude"]}
    assert score_importance(_item(category="backend", title="claude"), {}, kw) == "low"


# --- exclusion ----------------------------------------------------------------

def test_global_exclude_applies_to_every_category():
    exclude = {"global": ["fireside chat"]}
    for cat in ("ai", "security", "backend"):
        it = _item(category=cat, title="A Fireside Chat with the team")
        assert exclusion_hit(it, exclude) == "fireside chat"


def test_per_category_exclude_does_not_leak_across_categories():
    # the measured over-blocking hazard: `preview` must kill a frontend canary
    # release without touching a legitimate cloud announcement.
    exclude = {"frontend": ["preview"]}
    canary = _item(category="frontend", title="vercel/next.js v16.3.0-preview.9")
    cloud = _item(category="cloud",
                  title="PostgreSQL 19 Beta 2 is now available in Amazon RDS "
                        "Database Preview Environment")
    assert exclusion_hit(canary, exclude) == "preview"
    assert exclusion_hit(cloud, exclude) is None


def test_exclude_is_case_insensitive():
    assert exclusion_hit(_item(title="CANARY build"), {"global": ["canary"]}) == "canary"


def test_item_with_no_exclusion_match_is_kept():
    assert exclusion_hit(_item(title="Rails 8 released"), {"global": ["canary"]}) is None


def test_exclusion_hit_with_no_config_is_none():
    assert exclusion_hit(_item(title="anything"), None) is None
    assert exclusion_hit(_item(title="anything"), {}) is None
