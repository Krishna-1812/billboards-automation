"""Calling Claude, and keeping the cost ledger.

Every call goes through `complete_json()`: it asks for JSON that matches a schema (structured outputs), turns on
Anthropic's server-side fallback (a request a safety check declines is retried on another model, in the same
call), and writes one row to `ai_calls` with the tokens, the dollars, who caused it and which client it was for.
Tests swap the client with `set_client()`.
"""

import json
import logging

from . import runtime
from .models import AiCall

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# US dollars per million tokens: input, output, cache read. Cache writes cost 1.25× input.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}

_override = None


class AIUnavailable(Exception):
    pass


class AIFailed(Exception):
    pass


class AITransient(AIFailed):
    """Claude couldn't be reached (network, overload, rate limit): worth trying again later."""


def set_client(client):
    """Tests: a fake with `.beta.messages.create(**kwargs)`."""
    global _override
    _override = client


def available():
    return _override is not None or bool(runtime.config and runtime.config.ANTHROPIC_API_KEY_SET)


def _client():
    if _override is not None:
        return _override
    if not available():
        raise AIUnavailable("Claude isn't connected: set ANTHROPIC_API_KEY.")
    import anthropic
    return anthropic.Anthropic(max_retries=3, timeout=180.0)


def cost(model, usage):
    price = PRICES.get(model) or PRICES.get(runtime.config.PROPOSAL_MODEL if runtime.config else "") \
        or PRICES["claude-opus-5-5"]
    inp, out, cread = price
    tokens_in = getattr(usage, "input_tokens", 0) or 0
    tokens_out = getattr(usage, "output_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    usd = (tokens_in * inp + tokens_out * out + cw * inp * 1.25 + cr * cread) / 1_000_000
    return usd, tokens_in, tokens_out, cw, cr


def complete_json(session, *, purpose, system, content, schema, actor="agent", client_id=None,
                  proposal_id=None, eval_run_id=None, booking_id=None, creative_id=None, max_tokens=16000):
    """Ask Claude for JSON matching `schema`. Returns the parsed object. Every call is logged with its cost,
    including failed ones; raises AIFailed when there is no usable answer."""
    cfg = runtime.config
    model = cfg.PROPOSAL_MODEL if cfg else "claude-opus-5-5"
    effort = cfg.PROPOSAL_EFFORT if cfg else "medium"
    row = AiCall(purpose=purpose, model=model, actor=actor or "agent", client_id=client_id,
                 proposal_id=proposal_id, eval_run_id=eval_run_id, booking_id=booking_id, creative_id=creative_id)
    session.add(row)
    kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": content}],
                  output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}})
    if not model.startswith("claude-haiku"):          # Haiku has no server-side fallback
        kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
    try:
        import anthropic
        api_errors = (anthropic.APIError,)
    except ImportError:                                 # pragma: no cover
        api_errors = ()
    try:
        resp = _client().beta.messages.create(**kwargs)
    except AIUnavailable:
        session.expunge(row)
        raise
    except api_errors as e:
        row.ok, row.error = False, f"{type(e).__name__}: {e}"[:2000]
        raise AITransient(f"Claude couldn't be reached ({type(e).__name__}). It will be tried again.") from e
    served = getattr(resp, "model", None) or model
    row.model = served
    row.usd, row.input_tokens, row.output_tokens, row.cache_write_tokens, row.cache_read_tokens = \
        cost(served, resp.usage)
    row.stop_reason = resp.stop_reason or ""
    if resp.stop_reason == "refusal":
        row.ok = False
        details = getattr(resp, "stop_details", None)
        row.error = f"declined: {getattr(details, 'category', '') or ''}"
        raise AIFailed("Claude declined to read this message. A person needs to read it.")
    if resp.stop_reason == "max_tokens":
        row.ok = False
        row.error = "the answer was cut off (max_tokens)"
        raise AIFailed("The answer was cut off. The message may be very long.")
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    try:
        return json.loads(text)
    except ValueError as e:
        row.ok, row.error = False, f"not JSON: {text[:300]}"
        raise AIFailed("Claude's answer couldn't be read.") from e
