from __future__ import annotations
from datetime import datetime
from typing import Protocol
import httpx
from radar.item import Item
from radar.adapters.rss import RssAdapter
from radar.adapters.cloud import CloudAdapter
from radar.adapters.github import GithubAdapter
from radar.adapters.security import SecurityAdapter
from radar.adapters.social import SocialAdapter


class Adapter(Protocol):
    type: str
    def fetch(self, source: dict, cfg, *, client: httpx.Client, now: datetime) -> list[Item]: ...


ADAPTERS: dict[str, Adapter] = {a.type: a for a in [RssAdapter(), CloudAdapter(), GithubAdapter(), SecurityAdapter(), SocialAdapter()]}
