"""Optional live OpenRouter smoke test — requires OPENROUTER_API_KEY.

Run with:

    uv run --env-file .env pytest -m live -v

Normal `uv run pytest` will skip these.
"""

import os

import pytest

pytestmark = pytest.mark.live


@pytest.mark.live
@pytest.mark.asyncio
async def test_openrouter_live_smoke(tmp_path):
    # Skip cleanly if no key
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set — skipping live smoke test")

    # Use model from env or config's fast role
    smoke_model = os.getenv("OPENROUTER_SMOKE_MODEL")
    # Load config
    # Prefer example.yaml if exists
    import pathlib

    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.loader import load_config

    cfg_path = pathlib.Path("configs/example.yaml")
    if cfg_path.exists():
        cfg = load_config(cfg_path)
        # Override model if env var provided
        if smoke_model:
            for role in cfg.models.roles:
                cfg.models.roles[role].model = smoke_model
        # Use a temporary session root for test
        cfg.session.root = str(tmp_path / "sessions")
        # Minimal plugins for smoke: need openrouter, routing, session, loop, autonomy, echo
        # Ensure plugins list includes required ones
        required = {
            "model.openrouter",
            "routing.role_router",
            "session.jsonl",
            "loop.simple_tool_loop",
        }
        for r in required:
            if r not in cfg.plugins:
                cfg.plugins.append(r)
        # Autonomy and tool are optional but loop's default tools include echo
        if "tool.echo" not in cfg.plugins:
            cfg.plugins.append("tool.echo")
        if "autonomy.configurable" not in cfg.plugins:
            cfg.plugins.append("autonomy.configurable")
    else:
        # Fallback config
        from research_harness.config.loader import load_config_from_dict

        model = smoke_model or "deepseek/deepseek-v4-flash-0731"
        cfg = load_config_from_dict(
            {
                "plugins": [
                    "model.openrouter",
                    "routing.role_router",
                    "session.jsonl",
                    "autonomy.configurable",
                    "tool.echo",
                    "loop.simple_tool_loop",
                ],
                "models": {"roles": {"fast": {"provider": "openrouter", "model": model}}},
                "session": {"root": str(tmp_path / "sessions")},
                "loop": {"max_steps": 4},
            }
        )

    runtime = build_runtime(cfg)
    async with runtime:
        # Verify services are resolvable
        router = runtime.services.require("model_router.default")
        assert router is not None

        # Run a tiny request — do not assert exact natural language
        loop = runtime.services.require("agent_loop.default")
        # Use a prompt that does NOT require tool, to minimize tokens
        result = await loop.run("Say 'hello live' in 3 words.", role="fast")

        # Structural assertions
        assert isinstance(result.output, str)
        assert len(result.output) > 0, "model returned empty output"
        assert result.steps >= 1
        assert result.session_id is not None

        # Verify session persistence contains observable events
        store = runtime.services.require("session_store.default")
        events = await store.read(result.session_id)
        assert len(events) > 0
        types = {e["event_type"] for e in events}
        assert "run.started" in types
        assert "model.requested" in types
        assert "model.completed" in types
        assert "run.completed" in types

        # Check model metadata exists in completed event
        model_completed = [e for e in events if e["event_type"] == "model.completed"]
        assert model_completed, "no model.completed event"
        payload = model_completed[0]["payload"]
        assert payload.get("model") is not None, "model metadata missing"
        # Usage may be None for some free models, but if present should have total_tokens
        usage = payload.get("usage")
        if usage is not None:
            # At least one of prompt/completion/total should be present when usage exists
            assert any(
                usage.get(k) is not None
                for k in ("prompt_tokens", "completion_tokens", "total_tokens")
            )

        # Ensure no secrets leaked into persisted events
        import json

        dumped = json.dumps(events)
        assert "OPENROUTER_API_KEY" not in dumped
        assert (
            "sk-or-v1" not in dumped
            or "sk-or-v1" in os.getenv("OPENROUTER_API_KEY", "")
            and "sk-or-v1" not in dumped.replace(os.getenv("OPENROUTER_API_KEY", ""), "")
        ), "api key leaked into events"
        # Check Authorization header not present
        assert "Authorization" not in dumped or "Bearer" not in dumped
