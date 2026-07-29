import collections
from datetime import datetime, timezone, timedelta
from radar.item import Item
from radar.match import (term_hits, haystack, stack_matches, category_matches,
                         score_importance, exclusion_hit)
from radar.match import source_key, relevance_key
from radar.match import apply_source_fairness

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


# --- source_key -----------------------------------------------------------

def test_github_source_key_is_repo_scoped():
    assert source_key("https://github.com/vercel/next.js/releases/tag/v16") == \
        "github.com/vercel/next.js"
    # sibling repos must not pool into one bucket
    assert source_key("https://github.com/facebook/react/releases/tag/v19") == \
        "github.com/facebook/react"


def test_github_url_without_repo_path_falls_back_to_host():
    assert source_key("https://github.com/") == "github.com"


def test_www_prefix_stripped_from_source_key():
    assert source_key("https://www.docker.com/blog/x") == "docker.com"
    assert source_key("https://docker.com/blog/x") == "docker.com"


def test_source_key_is_case_insensitive_on_host():
    assert source_key("https://OpenAI.com/blog") == "openai.com"


def test_malformed_url_source_key_does_not_raise():
    for bad in ("", "not a url", "mailto:x@y.z", "https://[oops"):
        assert source_key(bad) == "(unknown)"


def test_relevance_key_ranks_more_matches_above_fewer():
    few = _item(id="few", importance="high", stack_match=["docker"])
    many = _item(id="many", importance="high",
                 stack_match=["docker"], keyword_match=["llm", "claude"])
    assert relevance_key(many) > relevance_key(few)


def test_relevance_key_ranks_importance_above_match_count():
    crit = _item(id="c", importance="critical")
    high = _item(id="h", importance="high", stack_match=["a", "b", "c"])
    assert relevance_key(crit) > relevance_key(high)


def test_relevance_key_breaks_equal_matches_by_recency():
    old = _item(id="o", importance="high", published=NOW - timedelta(days=1))
    new = _item(id="n", importance="high", published=NOW)
    assert relevance_key(new) > relevance_key(old)


# --- apply_source_fairness ----------------------------------------------------

def _card(id, host, n=0, published=None, severity=None):
    """A high-importance item from `host`, with `n` fake stack matches."""
    return _item(id=id, importance="high", url=f"https://{host}/{id}",
                 stack_match=[f"t{i}" for i in range(n)],
                 published=published or NOW, severity=severity)


def test_fairness_pass_demotes_nobody_when_under_budget():
    # one source holding every card; lopsidedness alone must not trigger trimming
    items = [_card(str(i), "openai.com") for i in range(10)]
    out, demoted = apply_source_fairness(items, budget=30, floor=3)
    assert demoted == {}
    assert all(it.demoted is None for it in out)


def test_fairness_pass_demotes_instead_of_dropping():
    items = [_card(str(i), "openai.com") for i in range(5)]
    out, demoted = apply_source_fairness(items, budget=3, floor=3)
    assert len(out) == 5                                   # nothing discarded
    assert sum(1 for it in out if it.demoted) == 2
    assert demoted == {"openai.com": 2}
    assert all(it.importance == "high" for it in out)       # importance untouched


def test_fairness_pass_guarantees_floor_to_every_source():
    # 3 sources x 4 cards = 12, budget 9, floor 3 -> each source keeps exactly 3
    items = [_card(f"{h}-{i}", h)
             for h in ("a.com", "b.com", "c.com") for i in range(4)]
    out, _ = apply_source_fairness(items, budget=9, floor=3)
    kept = collections.Counter(source_key(it.url) for it in out if not it.demoted)
    assert kept == {"a.com": 3, "b.com": 3, "c.com": 3}


def test_fairness_pass_does_not_pad_a_source_holding_fewer_than_the_floor():
    items = [_card("a1", "a.com")] + [_card(f"b{i}", "b.com") for i in range(5)]
    out, _ = apply_source_fairness(items, budget=4, floor=3)
    kept = collections.Counter(source_key(it.url) for it in out if not it.demoted)
    # a.com has only 1 item and keeps it; b.com takes its floor of 3
    assert kept == {"a.com": 1, "b.com": 3}


def test_fairness_pass_allocates_surplus_by_merit_ignoring_source():
    # floor 1 reserves 1 per source (2 slots), budget 4 leaves 2 merit slots.
    # b.com's leftovers have more matches, so it takes BOTH — no rotation.
    items = [_card("a1", "a.com", n=5), _card("a2", "a.com", n=0),
             _card("a3", "a.com", n=0),
             _card("b1", "b.com", n=4), _card("b2", "b.com", n=3),
             _card("b3", "b.com", n=2)]
    out, _ = apply_source_fairness(items, budget=4, floor=1)
    kept = {it.id for it in out if not it.demoted}
    assert kept == {"a1", "b1", "b2", "b3"}


def test_fairness_pass_fills_floors_level_by_level_when_floors_exceed_budget():
    # 4 sources x floor 3 = 12 reserved but budget is only 4, so the floor phase
    # must give every source its 1st item before anyone gets a 2nd.
    items = [_card(f"{h}-{i}", h, n=(3 - i))
             for h in ("a.com", "b.com", "c.com", "d.com") for i in range(3)]
    out, _ = apply_source_fairness(items, budget=4, floor=3)
    kept = collections.Counter(source_key(it.url) for it in out if not it.demoted)
    assert kept == {"a.com": 1, "b.com": 1, "c.com": 1, "d.com": 1}


def test_fairness_pass_never_demotes_high_severity_item():
    advisory = _card("cve", "openai.com", severity="critical")
    filler = [_card(str(i), "openai.com", n=5) for i in range(5)]
    out, _ = apply_source_fairness([advisory] + filler, budget=2, floor=0)
    kept = {it.id for it in out if not it.demoted}
    assert "cve" in kept


def test_fairness_pass_counts_exempt_items_against_the_budget():
    advisories = [_card(f"cve{i}", "a.com", severity="high") for i in range(3)]
    others = [_card(f"o{i}", "b.com") for i in range(3)]
    out, _ = apply_source_fairness(advisories + others, budget=3, floor=0)
    kept = {it.id for it in out if not it.demoted}
    assert kept == {"cve0", "cve1", "cve2"}   # budget consumed by the exempt items


def test_fairness_pass_exempt_items_satisfy_their_sources_floor():
    # a.com's 2 advisories already meet a floor of 2, so it reserves nothing more
    advisories = [_card(f"cve{i}", "a.com", severity="high") for i in range(2)]
    extra = [_card(f"a{i}", "a.com", n=5) for i in range(2)]
    others = [_card(f"b{i}", "b.com") for i in range(2)]
    out, _ = apply_source_fairness(advisories + extra + others, budget=4, floor=2)
    kept = collections.Counter(source_key(it.url) for it in out if not it.demoted)
    assert kept == {"a.com": 2, "b.com": 2}


def test_fairness_pass_ignores_medium_items():
    med = [_item(id=f"m{i}", importance="medium", url="https://a.com/x") for i in range(5)]
    cards = [_card(str(i), "a.com") for i in range(5)]
    out, demoted = apply_source_fairness(med + cards, budget=3, floor=3)
    assert all(it.demoted is None for it in out if it.importance == "medium")
    assert sum(demoted.values()) == 2       # only cards were trimmed


def test_fairness_pass_disabled_when_budget_zero():
    items = [_card(str(i), "a.com") for i in range(10)]
    out, demoted = apply_source_fairness(items, budget=0, floor=3)
    assert demoted == {} and all(it.demoted is None for it in out)


def test_fairness_pass_is_deterministic_for_equal_ranks():
    # identical rank on every item: repeated runs must demote the same ones
    items = [_card(f"{h}-{i}", h) for h in ("a.com", "b.com") for i in range(4)]
    first = {it.id for it in apply_source_fairness(items, 3, 1)[0] if not it.demoted}
    for _ in range(5):
        again = {it.id for it in apply_source_fairness(items, 3, 1)[0] if not it.demoted}
        assert again == first


def test_fairness_pass_never_removes_an_item():
    items = [_card(str(i), "a.com") for i in range(9)]
    out, _ = apply_source_fairness(items, budget=2, floor=1)
    assert {it.id for it in out} == {str(i) for i in range(9)}
