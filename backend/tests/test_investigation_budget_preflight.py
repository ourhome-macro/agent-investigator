from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from deerflow.agents.middlewares.token_budget_middleware import TokenBudgetMiddleware
from deerflow.config.token_budget_config import TokenBudgetConfig


class Request(SimpleNamespace):
    def override(self, **changes):
        return Request(**{**vars(self), **changes})


def test_preflight_refuses_oversized_input_without_calling_provider():
    middleware = TokenBudgetMiddleware(TokenBudgetConfig(enabled=True, max_tokens=1000, preflight=True))
    request = Request(messages=[HumanMessage(content="x" * 2000)], tools=[], system_message=None, runtime=SimpleNamespace(context={"run_id": "r1"}), model_settings={})
    with pytest.raises(RuntimeError, match="budget"):
        middleware.wrap_model_call(request, lambda _: pytest.fail("Provider must not be called"))


def test_preflight_caps_output_and_charges_ambiguous_failure():
    middleware = TokenBudgetMiddleware(TokenBudgetConfig(enabled=True, max_tokens=4000, preflight=True))
    request = Request(messages=[HumanMessage(content="hello")], tools=[], system_message=None, runtime=SimpleNamespace(context={"run_id": "r1"}), model_settings={"max_tokens": 9000})

    def provider(req):
        assert 0 < req.model_settings["max_tokens"] < 4000
        raise ValueError("Connection lost after request dispatch")

    with pytest.raises(ValueError):
        middleware.wrap_model_call(request, provider)
    with pytest.raises(RuntimeError, match="budget"):
        middleware.wrap_model_call(request, lambda _: pytest.fail("Ambiguous usage must stay reserved"))
