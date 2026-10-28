"""Drafting an agent calls the model, so one person gets a few in a row and a fixed number a day."""

import pytest

from loamz.engine.errors import RateLimitedError
from loamz.runtime.kernel.caller import Caller
from loamz.runtime.providers.limits.memory import MemoryCounters
from loamz.server.agents.bootstrap.settings import LlmSettings
from loamz.server.agents.features.directory.agents.draft import limit_drafts

ASHA = Caller(tenant_id="acme", user_id="asha")
BEN = Caller(tenant_id="acme", user_id="ben")


async def test_a_burst_of_drafts_is_refused_with_a_wait():
    counters, llm = MemoryCounters(), LlmSettings(drafts_per_10_minutes=2, drafts_per_day=30)
    await limit_drafts(counters, llm, ASHA)
    await limit_drafts(counters, llm, ASHA)
    with pytest.raises(RateLimitedError) as refused:
        await limit_drafts(counters, llm, ASHA)
    assert refused.value.retry_after and refused.value.retry_after > 0


async def test_the_day_has_a_fixed_number_and_a_refusal_does_not_use_one_up():
    counters, llm = MemoryCounters(), LlmSettings(drafts_per_10_minutes=50, drafts_per_day=3)
    for _ in range(3):
        await limit_drafts(counters, llm, ASHA)
    with pytest.raises(RateLimitedError, match="today's 3 drafts"):
        await limit_drafts(counters, llm, ASHA)
    assert (await counters.used_day("draft-day:acme:asha", limit=3)).used == 3


async def test_one_persons_drafts_do_not_use_up_anothers():
    counters, llm = MemoryCounters(), LlmSettings(drafts_per_10_minutes=1, drafts_per_day=1)
    await limit_drafts(counters, llm, ASHA)
    await limit_drafts(counters, llm, BEN)
