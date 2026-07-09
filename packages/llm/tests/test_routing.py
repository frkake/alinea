"""RouteResolver / routing.yaml の検証(plans/04 §8・§15・§17-7)。"""

from __future__ import annotations

from pathlib import Path

from alinea_llm.routing import TASKS, RouteResolver, RoutingConfig

_ROUTING_YAML = Path(__file__).resolve().parents[1] / "routing.yaml"


def _config() -> RoutingConfig:
    return RoutingConfig.from_yaml(_ROUTING_YAML)


def test_routing_yaml_defines_all_eight_tasks() -> None:
    cfg = _config()
    assert set(cfg.tasks.keys()) == set(TASKS)
    assert cfg.tasks["translation"].chain[0] == "deepseek-v4-flash"
    assert cfg.tasks["retranslation_escalation"].chain[0] == "claude-sonnet-5"
    assert cfg.retry_defaults.max_attempts == 3
    assert cfg.retry_defaults.retry_after_cap_s == 30


def test_chain_for_default() -> None:
    resolver = RouteResolver(_config())
    assert resolver.chain_for("chat") == ["claude-opus-4-8", "gpt-5.5", "gemini-3.5-flash"]


def test_chain_for_user_override_inserted_first() -> None:
    # §15: ユーザー上書きは先頭に挿入(選択モデルが既定チェーンに含まれる場合は移動)
    resolver = RouteResolver(_config(), overrides={("u1", "chat"): "gemini-3.5-flash"})
    assert resolver.chain_for("chat", "u1") == ["gemini-3.5-flash", "claude-opus-4-8", "gpt-5.5"]


def test_chain_for_excludes_unconfigured_and_disabled_providers() -> None:
    # §11.1-3 / §15: 運営キー未設定プロバイダのモデルを除外
    model_provider = {
        "deepseek-v4-flash": "deepseek",
        "gemini-3.5-flash": "google",
        "gpt-5.4-mini": "openai",
    }
    resolver = RouteResolver(
        _config(),
        available_providers={"google", "openai"},  # deepseek のキー未設定
        model_provider=model_provider,
    )
    assert resolver.chain_for("translation") == ["gemini-3.5-flash", "gpt-5.4-mini"]
