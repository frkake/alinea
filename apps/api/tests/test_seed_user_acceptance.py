"""apps/api/scripts/seed_user_acceptance.py のテスト(Task 32・Step 2)。

検証(ブリーフの Expected):
- review 環境で実行でき、UAT-A/UAT-B を初期化する(presentation ルート・予算・0600 出力)。
- review 以外の環境では実行を拒否する。
- 予約済み(UAT-A/UAT-B)以外のユーザーは 1 件も変更されない。
- 固定 URL/期待値フィクスチャの検証(schema version・source id 重複・URL 形式)。

外部通信は一切しない(review 環境ガード + 実 PostgreSQL/Redis のみ)。
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.db.models import User
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

# scripts/ はパッケージではないためファイルパスから import する。
_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "seed_user_acceptance.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("seed_user_acceptance", _SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["seed_user_acceptance"] = module
    spec.loader.exec_module(module)
    return module


seed_mod = _load_module()


@pytest.fixture
def review_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """APP_ENV=review にし、settings キャッシュをクリアする(前後で復元)。"""
    from alinea_api.settings import get_api_settings
    from alinea_core.settings import get_settings

    monkeypatch.setenv("APP_ENV", "review")
    get_settings.cache_clear()
    get_api_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_api_settings.cache_clear()


# --------------------------------------------------------------------------- #
# フィクスチャ検証(純関数・DB 非依存)
# --------------------------------------------------------------------------- #
def test_fixtures_load_and_validate() -> None:
    data = seed_mod.load_fixtures()
    assert data["schema_version"] == seed_mod.EXPECTED_FIXTURE_SCHEMA_VERSION
    ids = [s["id"] for s in data["sources"]]
    assert len(ids) == len(set(ids)), "source id は一意でなければならない"
    # 期待識別子(チェックリストの固定値)が読める。
    by_id = {s["id"]: s for s in data["sources"]}
    assert by_id["ARXIV-ATTENTION"]["expected"]["arxiv_id"] == "1706.03762"


def test_fixtures_reject_bad_schema_version(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": 999, "sources": []}), encoding="utf-8")
    with pytest.raises(seed_mod.SeedError, match="schema_version"):
        seed_mod.load_fixtures(bad)


def test_fixtures_reject_duplicate_source_id(tmp_path: Path) -> None:
    bad = tmp_path / "dup.json"
    bad.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {"id": "X", "input_url": "https://a.test/1", "expected": {"k": 1}},
                    {"id": "X", "input_url": "https://a.test/2", "expected": {"k": 2}},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(seed_mod.SeedError, match="重複"):
        seed_mod.load_fixtures(bad)


def test_fixtures_reject_bad_url(tmp_path: Path) -> None:
    bad = tmp_path / "url.json"
    bad.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [{"id": "X", "input_url": "not-a-url", "expected": {"k": 1}}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(seed_mod.SeedError, match="input_url"):
        seed_mod.load_fixtures(bad)


# --------------------------------------------------------------------------- #
# 環境ガード
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_refuses_outside_review_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alinea_api.settings import get_api_settings
    from alinea_core.settings import get_settings

    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    get_api_settings.cache_clear()
    try:
        with pytest.raises(seed_mod.SeedError, match="review"):
            await seed_mod.seed_user_acceptance(reset=True, output=tmp_path / "out.json")
        assert not (tmp_path / "out.json").exists(), "拒否時は出力ファイルを作らない"
    finally:
        get_settings.cache_clear()
        get_api_settings.cache_clear()


# --------------------------------------------------------------------------- #
# UAT 設定の構築(純関数)
# --------------------------------------------------------------------------- #
def test_build_uat_settings_openai() -> None:
    s = seed_mod.build_uat_settings("gpt-5.5")
    assert s["llm_routing"]["presentation"] == {"provider": "openai", "model": "gpt-5.5"}
    assert s["code_analysis"]["monthly_budget_usd"] == "5.00"


def test_build_uat_settings_anthropic() -> None:
    s = seed_mod.build_uat_settings("claude-opus-4-8")
    assert s["llm_routing"]["presentation"] == {
        "provider": "anthropic",
        "model": "claude-opus-4-8",
    }
    assert s["code_analysis"]["monthly_budget_usd"] == "5.00"


# --------------------------------------------------------------------------- #
# フルシード(review 環境・実 DB)
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def outsider(db_session: AsyncSession) -> AsyncIterator[tuple[str, str]]:
    """予約外ユーザー(seed が触れてはならない対照群)。"""
    email = f"outsider-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    user.settings = {"sentinel": "OUTSIDER_KEEP"}
    db_session.add(user)
    await db_session.commit()
    uid = str(user.id)
    try:
        yield uid, email
    finally:
        await db_session.rollback()
        await purge_user(db_session, uid)


@pytest.mark.asyncio
async def test_seed_initializes_reserved_accounts(
    review_env: None, db_session: AsyncSession, outsider: tuple[str, str], tmp_path: Path
) -> None:
    outsider_id, _ = outsider
    out = tmp_path / "uat-accounts.json"

    payload = await seed_mod.seed_user_acceptance(reset=True, output=out)

    try:
        # 出力ファイルは mode 0600。
        mode = stat.S_IMODE(os.stat(out).st_mode)
        assert mode == 0o600, f"出力は 0600 でなければならない(実際: {oct(mode)})"

        # 2 アカウント + ワンタイムトークン + ルート/予算。
        accounts = {a["label"]: a for a in payload["accounts"]}
        assert set(accounts) == {"UAT-A", "UAT-B"}
        assert accounts["UAT-A"]["presentation_route"] == {
            "provider": "openai",
            "model": "gpt-5.5",
        }
        assert accounts["UAT-B"]["presentation_route"] == {
            "provider": "anthropic",
            "model": "claude-opus-4-8",
        }
        for acc in accounts.values():
            assert acc["one_time_password"], "ワンタイムトークンが必要"
            assert "/api/auth/email/verify?token=" in acc["login_url"]
            assert acc["code_analysis_monthly_budget_usd"] == "5.00"

        # DB 反映: user_task_model_overrides(presentation)+ settings。
        for email, model in (
            (seed_mod.UAT_A_EMAIL, "gpt-5.5"),
            (seed_mod.UAT_B_EMAIL, "claude-opus-4-8"),
        ):
            user = (
                await db_session.execute(select(User).where(User.email == email))
            ).scalar_one()
            assert user.settings["code_analysis"]["monthly_budget_usd"] == "5.00"
            assert user.settings["llm_routing"]["presentation"]["model"] == model
            override = (
                await db_session.execute(
                    text(
                        "SELECT model_id FROM user_task_model_overrides "
                        "WHERE user_id = CAST(:u AS uuid) AND task = 'presentation'"
                    ),
                    {"u": str(user.id)},
                )
            ).scalar_one()
            assert override == model

        # 予約外ユーザーは 1 バイトも変わらない。
        outsider_row: User | None = await db_session.get(User, outsider_id)
        assert outsider_row is not None, "予約外ユーザーが削除されてはならない"
        await db_session.refresh(outsider_row)
        assert outsider_row.settings == {"sentinel": "OUTSIDER_KEEP"}

        # トークンは出力ファイルにだけあり、ログ相当(payload note)には露出しない。
        assert "token=" not in payload["note"]
    finally:
        # 予約アカウントを後片付け(他テストの決定性のため)。
        for email in seed_mod.RESERVED_EMAILS:
            reserved = (
                await db_session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if reserved is not None:
                await purge_user(db_session, str(reserved.id))


@pytest.mark.asyncio
async def test_seed_reset_is_idempotent(
    review_env: None, db_session: AsyncSession, tmp_path: Path
) -> None:
    """--reset を 2 回流しても UAT アカウントは 2 件のまま(重複しない)。"""
    out = tmp_path / "a.json"
    await seed_mod.seed_user_acceptance(reset=True, output=out)
    first_ids = {
        email: (
            await db_session.execute(select(User.id).where(User.email == email))
        ).scalar_one()
        for email in seed_mod.RESERVED_EMAILS
    }
    await seed_mod.seed_user_acceptance(reset=True, output=tmp_path / "b.json")
    try:
        assert set(first_ids) == set(seed_mod.RESERVED_EMAILS)
        for email in seed_mod.RESERVED_EMAILS:
            rows = (
                (await db_session.execute(select(User.id).where(User.email == email)))
                .scalars()
                .all()
            )
            # --reset は作り直すため id は変わってよいが、重複しない(常に 1 行)ことが要件。
            assert len(rows) == 1, f"{email} は 1 行のみ(重複しない)"
    finally:
        for email in seed_mod.RESERVED_EMAILS:
            reserved = (
                await db_session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if reserved is not None:
                await purge_user(db_session, str(reserved.id))
