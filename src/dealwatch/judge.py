from .extract import extract
from .models import Decision, utcnow
from .value import decide, excluded


async def judge(client, config, deal, history, now=None):
    now = now or utcnow()
    if reason := deal.unavailable(now):
        return None, Decision(kind="none", reason=reason)
    if excluded(deal, config):
        return None, Decision(kind="none", reason="Excluded category")
    ex, model, latency = await extract(client, config, deal)
    decision = decide(deal, ex, history, config, now)
    decision.model = model
    decision.latency_ms = latency
    return ex, decision
