"""Supported brokers: catalogue, verification status and client construction.

Only brokers with an implemented client can be connected. A broker integration that has
not been exercised against the broker's live API is marked UNVERIFIED_AGAINST_LIVE_API and
is disabled unless its setting enables it. No integration may place orders in this build.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.brokers.client import AuthFlow, BrokerCapabilities, BrokerClient, VerificationStatus
from app.brokers.indstocks import CAPABILITIES as INDSTOCKS_CAPABILITIES
from app.brokers.indstocks import IndstocksBrokerClient
from app.core.config import Settings

# Tests set this to an httpx.MockTransport so no network is ever used.
transport_override: httpx.BaseTransport | None = None


@dataclass(frozen=True)
class BrokerInfo:
    id: str
    name: str
    auth_flow: AuthFlow | None
    verification: VerificationStatus
    capabilities: BrokerCapabilities | None
    docs_url: str | None
    token_ttl_hours: int | None = None
    sources: tuple[str, ...] = field(default_factory=tuple)
    enabled_setting: str | None = None

    def enabled(self, settings: Settings) -> bool:
        return bool(self.enabled_setting and getattr(settings, self.enabled_setting, False))


INDSTOCKS = BrokerInfo(
    id="indstocks", name="INDstocks (INDmoney)", auth_flow=AuthFlow.TOKEN_PASTE,
    verification=VerificationStatus.UNVERIFIED_AGAINST_LIVE_API,
    capabilities=INDSTOCKS_CAPABILITIES, docs_url="https://api-docs.indstocks.com/",
    token_ttl_hours=24,
    sources=("https://api-docs.indstocks.com/Users/",
             "https://api-docs.indstocks.com/normal_orders/",
             "https://api-docs.indstocks.com/portfolio_funds/",
             "https://api-docs.indstocks.com/conventions/"),
    enabled_setting="broker_indstocks_enabled")

# Listed for transparency; nothing is implemented for these.
PLANNED = (("zerodha", "Zerodha Kite Connect"), ("angelone", "Angel One SmartAPI"),
           ("upstox", "Upstox API"), ("dhan", "Dhan HQ API"))

BROKERS: dict[str, BrokerInfo] = {
    INDSTOCKS.id: INDSTOCKS,
    **{i: BrokerInfo(id=i, name=n, auth_flow=None,
                     verification=VerificationStatus.NOT_INTEGRATED, capabilities=None,
                     docs_url=None) for i, n in PLANNED},
}


def _indstocks(settings: Settings, token: str) -> IndstocksBrokerClient:
    return IndstocksBrokerClient(token=token, base_url=settings.indstocks_base_url,
                                 product=settings.broker_indstocks_product,
                                 timeout=settings.indstocks_timeout_seconds,
                                 transport=transport_override)


FACTORIES: dict[str, Callable[[Settings, str], Any]] = {"indstocks": _indstocks}


def get_info(broker: str) -> BrokerInfo | None:
    return BROKERS.get(broker)


def build_client(settings: Settings, broker: str, token: str) -> BrokerClient:
    factory = FACTORIES.get(broker)
    if factory is None:
        raise LookupError(f"No client is implemented for broker {broker!r}.")
    return factory(settings, token)
