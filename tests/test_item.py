from datetime import datetime, timezone
from radar.item import Item, item_id, IMPORTANCE_ORDER


def test_item_id_is_stable_16_hex():
    a = item_id("https://example.com/x")
    b = item_id("https://example.com/x")
    assert a == b and len(a) == 16
    assert a != item_id("https://example.com/y")


def test_importance_order_ranks_low_to_critical():
    assert IMPORTANCE_ORDER["low"] < IMPORTANCE_ORDER["medium"] < \
        IMPORTANCE_ORDER["high"] < IMPORTANCE_ORDER["critical"]


def test_item_defaults():
    it = Item(id="1", title="t", url="u", source_type="rss", category="backend",
              published=datetime(2026, 7, 17, tzinfo=timezone.utc), summary="s")
    assert it.importance == "low"
    assert it.tags == [] and it.stack_match == []
    assert it.provider is None and it.severity is None and it.llm is None
