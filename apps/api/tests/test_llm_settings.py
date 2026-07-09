"""LLM ルーティング・BYOK・クォータ(M0-13 / plans/04 §9・§11・§15、plans/07 §9)。

- PY-SET-02: BYOK Fernet 暗号化往復・末尾4文字マスク・MultiFernet ローテーション。
- PY-SET-03: 月次クォータ超過で 429 quota_exceeded・BYOK 設定時はスキップ(非消費)。
- PY-SET-04: DB ルート解決(既定チェーン・ユーザー上書き先頭挿入・disabled/未設定除外)。
"""

from __future__ import annotations

import uuid

import pytest
from alinea_api.errors import ProblemException
from alinea_api.llm.deps import build_router_for_user, check_quota
from alinea_api.llm.key_store import DbKeyStore
from alinea_api.llm.meter import DbMeterHook
from alinea_api.llm.route_store import DbRouteStore
from alinea_api.settings import ApiSettings
from alinea_llm.testing.fake_provider import FakeLLMProvider
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def _new_user(session: AsyncSession) -> str:
    uid = (
        await session.execute(
            text("INSERT INTO users (email) VALUES (:email) RETURNING id"),
            {"email": f"llm-{uuid.uuid4().hex}@example.com"},
        )
    ).scalar_one()
    return str(uid)


def _settings(**overrides: str) -> ApiSettings:
    # alinea_key_encryption_secret などは .env から読む。運営キーだけ上書きする。
    return ApiSettings(**overrides)  # type: ignore[arg-type]


def _fake_factory(provider: str, api_key: str) -> FakeLLMProvider:
    return FakeLLMProvider(name=provider)


# ---------------------------------------------------------------------------
# PY-SET-02: BYOK 暗号化往復・マスク・ローテーション
# ---------------------------------------------------------------------------
async def test_byok_key_roundtrip_and_mask(db_session: AsyncSession) -> None:
    user_id = await _new_user(db_session)
    ks = DbKeyStore(db_session)

    await ks.put(user_id=user_id, provider="openai", plaintext="sk-secret-1234")
    got = await ks.get(user_id=user_id, provider="openai")
    assert got == "sk-secret-1234"  # 復号往復

    masked = await ks.mask(user_id=user_id, provider="openai")
    assert masked is not None
    assert masked.endswith("1234")
    assert "secret" not in masked
    assert masked.startswith("sk-…")  # plans/04 §11.3 の逐語プレフィックス


async def test_byok_encrypted_at_rest_and_reput_overwrites(db_session: AsyncSession) -> None:
    user_id = await _new_user(db_session)
    ks = DbKeyStore(db_session)
    await ks.put(user_id=user_id, provider="anthropic", plaintext="sk-ant-abcd")

    # 保存カラムに平文が残っていない(暗号化されている)。
    row = (
        await db_session.execute(
            text(
                "SELECT encrypted_key, key_hint, status FROM byok_api_keys "
                "WHERE user_id = CAST(:u AS uuid) AND provider = 'anthropic'"
            ),
            {"u": user_id},
        )
    ).first()
    assert row is not None
    assert b"sk-ant-abcd" not in bytes(row[0])
    assert row[1] == "abcd"
    assert row[2] == "untested"

    # 再入力で上書きされ status は untested に戻る。
    await ks.put(user_id=user_id, provider="anthropic", plaintext="sk-ant-wxyz")
    assert await ks.get(user_id=user_id, provider="anthropic") == "sk-ant-wxyz"


async def test_byok_multifernet_rotation_decrypts_old_token(db_session: AsyncSession) -> None:
    user_id = await _new_user(db_session)
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    # 旧マスタキーだけで暗号化保存。
    old_store = DbKeyStore(db_session, _settings(alinea_key_encryption_secret=old_key))
    await old_store.put(user_id=user_id, provider="deepseek", plaintext="sk-old-token")

    # ローテーション後(新キー先頭・旧キーも保持)でも復号できる(§11.2)。
    rotated = DbKeyStore(
        db_session, _settings(alinea_key_encryption_secret=f"{new_key},{old_key}")
    )
    assert await rotated.get(user_id=user_id, provider="deepseek") == "sk-old-token"


async def test_byok_active_providers_delete_and_mark_invalid(db_session: AsyncSession) -> None:
    user_id = await _new_user(db_session)
    ks = DbKeyStore(db_session)
    await ks.put(user_id=user_id, provider="openai", plaintext="sk-a-1234")
    await ks.put(user_id=user_id, provider="google", plaintext="AIza-5678")

    assert await ks.active_providers(user_id) == {"openai", "google"}

    # §11.4: 失効すると active から外れる(行は残す=設定画面で「無効」表示)。
    await ks.mark_invalid(user_id, "google")
    assert await ks.active_providers(user_id) == {"openai"}

    # 削除すると get は None(以後は運営キー)。
    await ks.delete(user_id, "openai")
    assert await ks.get(user_id, "openai") is None
    assert await ks.active_providers(user_id) == set()


async def test_get_and_mask_absent_key_returns_none(db_session: AsyncSession) -> None:
    user_id = await _new_user(db_session)
    ks = DbKeyStore(db_session)
    assert await ks.get(user_id, "xai") is None
    assert await ks.mask(user_id, "xai") is None


async def test_quota_check_without_byok_does_not_require_fernet_secret(
    db_session: AsyncSession,
) -> None:
    user_id = await _new_user(db_session)
    settings = _settings(
        alinea_key_encryption_secret="not-a-fernet-key",
        openai_api_key="sk-operator",
    )
    await check_quota(db_session, user_id, "article", settings=settings)


# ---------------------------------------------------------------------------
# PY-SET-04: DB ルート解決
# ---------------------------------------------------------------------------
async def test_route_default_chain(db_session: AsyncSession) -> None:
    store = DbRouteStore(db_session)  # cache=None(テストは DB 直参照)
    assert await store.chain_for("chat") == ["claude-opus-4-8", "gpt-5.5", "gemini-3.5-flash"]
    assert await store.primary_provider("chat") == "anthropic"


async def test_route_user_override_inserted_first(db_session: AsyncSession) -> None:
    user_id = await _new_user(db_session)
    await db_session.execute(
        text(
            "INSERT INTO user_task_model_overrides (user_id, task, model_id) "
            "VALUES (CAST(:u AS uuid), 'chat', 'gemini-3.5-flash')"
        ),
        {"u": user_id},
    )
    store = DbRouteStore(db_session)
    # §15: ユーザー選択モデルを先頭へ(既定チェーンにあれば移動)。
    assert await store.chain_for("chat", user_id) == [
        "gemini-3.5-flash",
        "claude-opus-4-8",
        "gpt-5.5",
    ]
    assert await store.primary_provider("chat", user_id) == "google"


async def test_route_excludes_disabled_and_unconfigured(db_session: AsyncSession) -> None:
    # disabled モデルは除外(セッション内 UPDATE・ロールバックで復元)。
    await db_session.execute(text("UPDATE llm_models SET enabled = false WHERE id = 'gpt-5.5'"))
    store = DbRouteStore(db_session)
    assert await store.chain_for("chat") == ["claude-opus-4-8", "gemini-3.5-flash"]

    # available_providers 未指定プロバイダのモデルを除外(§15・§11.1-3)。
    assert await store.chain_for("chat", available_providers={"google"}) == ["gemini-3.5-flash"]


# ---------------------------------------------------------------------------
# PY-SET-03: 月次クォータ
# ---------------------------------------------------------------------------
async def _insert_usage(
    session: AsyncSession, user_id: str, *, task: str, key_source: str = "operator", n: int = 1
) -> None:
    for _ in range(n):
        await session.execute(
            text(
                "INSERT INTO usage_records (user_id, task, provider, model, key_source, status) "
                "VALUES (CAST(:u AS uuid), :task, 'anthropic', 'claude-opus-4-8', :ks, 'ok')"
            ),
            {"u": user_id, "task": task, "ks": key_source},
        )


async def test_quota_exceeded_returns_429(db_session: AsyncSession) -> None:
    user_id = await _new_user(db_session)
    # 上限をテスト用に 2 件へ(セッション内・ロールバックで復元)。
    await db_session.execute(
        text("UPDATE quota_limits SET monthly_limit = 2 WHERE key = 'chat_messages'")
    )
    await _insert_usage(db_session, user_id, task="chat", n=2)

    with pytest.raises(ProblemException) as exc:
        await check_quota(db_session, user_id, "chat")
    assert exc.value.code == "quota_exceeded"
    assert exc.value.status == 429
    assert "BYOK" in (exc.value.detail or "")

    # 上限を上げれば通る。
    await db_session.execute(
        text("UPDATE quota_limits SET monthly_limit = 3 WHERE key = 'chat_messages'")
    )
    await check_quota(db_session, user_id, "chat")  # 例外なし


async def test_quota_only_counts_operator_ok_rows(db_session: AsyncSession) -> None:
    user_id = await _new_user(db_session)
    await db_session.execute(
        text("UPDATE quota_limits SET monthly_limit = 2 WHERE key = 'chat_messages'")
    )
    # BYOK(user)行はクォータ非消費(§10.2)。2 件あっても消費 0。
    await _insert_usage(db_session, user_id, task="chat", key_source="user", n=2)
    await check_quota(db_session, user_id, "chat")  # 例外なし


async def test_quota_skipped_when_byok_active_for_primary(db_session: AsyncSession) -> None:
    user_id = await _new_user(db_session)
    await db_session.execute(
        text("UPDATE quota_limits SET monthly_limit = 1 WHERE key = 'chat_messages'")
    )
    await _insert_usage(db_session, user_id, task="chat", n=5)  # 上限超過状態
    # chat の先頭プロバイダ(anthropic)に BYOK があると事前チェックをスキップ(§9.2)。
    ks = DbKeyStore(db_session)
    await ks.put(user_id=user_id, provider="anthropic", plaintext="sk-ant-user")
    await check_quota(db_session, user_id, "chat")  # 例外なし


async def test_quota_untracked_task_is_noop(db_session: AsyncSession) -> None:
    user_id = await _new_user(db_session)
    # translation は waiting_quota 管轄(plans/03 §17.4)。check_quota は 429 判定しない。
    await check_quota(db_session, user_id, "translation")


# ---------------------------------------------------------------------------
# 計測フック + ルータ構築(BYOK/operator の key_source 帰属)
# ---------------------------------------------------------------------------
async def test_meter_records_row_with_byok_key_source(db_session: AsyncSession) -> None:
    user_id = await _new_user(db_session)
    from alinea_llm.protocols import UsageDraft
    from alinea_llm.types import Usage

    meter = DbMeterHook(db_session, byok_providers={"anthropic"})
    await meter.record(
        UsageDraft(
            user_id=user_id,
            task="chat",
            provider="anthropic",
            model="claude-opus-4-8",
            key_source="operator",  # ルータは常に operator。BYOK ありなら user に補正。
            usage=Usage(input_tokens=100, output_tokens=50),
            cost_usd=0.00305,
            status="ok",
        )
    )
    row = (
        await db_session.execute(
            text(
                "SELECT key_source, input_tokens, output_tokens, cost_usd, status "
                "FROM usage_records WHERE user_id = CAST(:u AS uuid)"
            ),
            {"u": user_id},
        )
    ).first()
    assert row is not None
    assert row[0] == "user"  # BYOK provider は user に補正
    assert row[1] == 100
    assert row[2] == 50
    assert float(row[3]) == pytest.approx(0.00305)


async def test_build_router_operator_then_byok_key_source(db_session: AsyncSession) -> None:
    user_id = await _new_user(db_session)
    settings = _settings(anthropic_api_key="sk-operator-key")

    # 運営キーのみ: チェーンは anthropic のみに絞られ、key_source=operator。
    router = await build_router_for_user(
        db_session, user_id, "chat", settings=settings, provider_factory=_fake_factory
    )
    resp = await router.complete("chat", prompt="hello", user_id=user_id)
    assert resp.provider == "anthropic"
    assert resp.model == "claude-opus-4-8"
    src = (
        await db_session.execute(
            text(
                "SELECT key_source FROM usage_records "
                "WHERE user_id = CAST(:u AS uuid) AND status = 'ok' ORDER BY id DESC LIMIT 1"
            ),
            {"u": user_id},
        )
    ).scalar_one()
    assert src == "operator"

    # BYOK 登録後: 同タスクの記録は user(クォータ非消費)。
    await DbKeyStore(db_session, settings).put(
        user_id=user_id, provider="anthropic", plaintext="sk-ant-user"
    )
    router2 = await build_router_for_user(
        db_session, user_id, "chat", settings=settings, provider_factory=_fake_factory
    )
    await router2.complete("chat", prompt="hello again", user_id=user_id)
    src2 = (
        await db_session.execute(
            text(
                "SELECT key_source FROM usage_records "
                "WHERE user_id = CAST(:u AS uuid) AND status = 'ok' ORDER BY id DESC LIMIT 1"
            ),
            {"u": user_id},
        )
    ).scalar_one()
    assert src2 == "user"
