"""再現可能なユーザー受け入れ(UAT)環境を作るシード(Task 32・Step 2)。

    uv run python apps/api/scripts/seed_user_acceptance.py --reset --output <path>

**目的**: 最終ユーザー受け入れチェックリスト(docs/superpowers/plans/
2026-07-17-user-acceptance-checklist.md §3)が要求する 2 つの予約済みアカウント
UAT-A / UAT-B を、確認者ごとにばらつかない固定条件で初期化する。

**この seed が作るもの(予約済みアカウントのみ)**:
- UAT-A(``uat-a@alinea.review``)/ UAT-B(``uat-b@alinea.review``)の users 行 + email identity。
- PPTX 生成ルート: UAT-A は OpenAI(``gpt-5.5``)、UAT-B は Anthropic(``claude-opus-4-8``)を
  ``user_task_model_overrides(task='presentation')`` へ設定する(実行時ルート = DB が正)。
  併せて ``users.settings.llm_routing.presentation`` にも同じ選択を保存する(設定画面表示用)。
- GitHub コード解析予算: 両者とも ``settings.code_analysis.monthly_budget_usd = 5.00``。
- ログインは **ワンタイムのメールリンクトークン**(session_service。単回消費・15 分有効)を
  実行時に生成して出力ファイルへ書く。確認者はこの URL を 1 回開くとセッションが張られる。
  パスワード相当のこのトークンは **ログにも repo にも残さず**、mode 0600 の JSON にだけ書く。

**安全策(hard constraints)**:
- review 環境(``APP_ENV=review``)でのみ実行できる。それ以外は即座に拒否する。
- 予約済み 2 ユーザー以外のユーザー・データには一切触れない(--reset も予約アカウントのみ)。
- 外部論文はここでは取り込まない。確認者がチェックリストの固定 URL を手で入力する。
- 固定 URL / 期待値は ``docs/superpowers/plans/2026-07-17-user-acceptance-fixtures.json``
  から読み、schema version・source id 重複・URL 形式・期待識別子を検証する(破損した
  フィクスチャで確認を始めないため)。
- 生成物 JSON は mode 0600。ワンタイムトークンは stdout・ログへ出さない。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# ローカル HTTP(Redis/MinIO)はプロキシを迂回する(企業プロキシ環境。plans/00・MEMORY)。
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1")

REVIEW_APP_ENV = "review"

# 予約済み UAT アカウント(この 2 件だけを初期化する)。
UAT_A_EMAIL = "uat-a@alinea.review"
UAT_B_EMAIL = "uat-b@alinea.review"
RESERVED_EMAILS = (UAT_A_EMAIL, UAT_B_EMAIL)

# PPTX(presentation)ルートのユーザー別モデル。models.yaml のシード ID と一致させる。
PRESENTATION_TASK = "presentation"
UAT_A_PRESENTATION = ("openai", "gpt-5.5")
UAT_B_PRESENTATION = ("anthropic", "claude-opus-4-8")
CODE_ANALYSIS_BUDGET_USD = "5.00"

FIXTURES_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-07-17-user-acceptance-fixtures.json"
)
EXPECTED_FIXTURE_SCHEMA_VERSION = 1


class SeedError(RuntimeError):
    """seed 実行を安全に中断するためのドメインエラー。"""


# --------------------------------------------------------------------------- #
# フィクスチャ検証
# --------------------------------------------------------------------------- #
def _validate_fixtures(data: dict[str, Any]) -> None:
    """schema version・source id 重複・URL 形式・期待識別子を検証する。"""
    version = data.get("schema_version")
    if version != EXPECTED_FIXTURE_SCHEMA_VERSION:
        raise SeedError(
            f"fixtures schema_version={version!r} は"
            f"期待値 {EXPECTED_FIXTURE_SCHEMA_VERSION} と不一致"
        )
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise SeedError("fixtures.sources が空、または配列ではない")

    seen_ids: set[str] = set()
    for src in sources:
        sid = src.get("id")
        if not isinstance(sid, str) or not sid:
            raise SeedError(f"source id が不正: {src!r}")
        if sid in seen_ids:
            raise SeedError(f"source id 重複: {sid}")
        seen_ids.add(sid)

        url = src.get("input_url")
        if not isinstance(url, str):
            raise SeedError(f"{sid}: input_url が無い")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise SeedError(f"{sid}: input_url の形式が不正: {url}")

        # 期待識別子(expected / expected_mappings / error_class)のいずれかを必須にする。
        if not (src.get("expected") or src.get("expected_mappings") or src.get("verified_commit")):
            raise SeedError(f"{sid}: 期待識別子(expected 等)が無い")


def load_fixtures(path: Path = FIXTURES_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise SeedError(f"fixtures が見つからない: {path}")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    _validate_fixtures(data)
    return data


# --------------------------------------------------------------------------- #
# 環境ガード
# --------------------------------------------------------------------------- #
def _require_review_env() -> None:
    """review 環境でのみ実行を許可する(それ以外は拒否)。"""
    from alinea_core.settings import get_settings

    app_env = get_settings().app_env
    if app_env != REVIEW_APP_ENV:
        raise SeedError(
            f"seed_user_acceptance は review 環境専用です(現在 APP_ENV={app_env!r})。"
            f" APP_ENV={REVIEW_APP_ENV} を設定した確認用環境で実行してください。"
        )


# --------------------------------------------------------------------------- #
# ユーザー別 UAT 設定の構築
# --------------------------------------------------------------------------- #
def build_uat_settings(presentation_model: str) -> dict[str, Any]:
    """UAT ユーザーの users.settings(完全形)を返す。

    presentation ルートと code_analysis 予算だけを上書きし、他は既定値。
    """
    from alinea_api.schemas.settings import DEFAULTS, FullSettings, deep_merge

    patch: dict[str, Any] = {
        "llm_routing": {
            "presentation": {
                "provider": _provider_for_model(presentation_model),
                "model": presentation_model,
            }
        },
        "code_analysis": {"monthly_budget_usd": CODE_ANALYSIS_BUDGET_USD},
    }
    merged = deep_merge(DEFAULTS, patch)
    # 値域検証(不正なら例外)。Decimal を JSON セーフな文字列へ直すため model_dump を通す。
    return FullSettings.model_validate(merged).model_dump()


def _provider_for_model(model_id: str) -> str:
    if model_id == UAT_A_PRESENTATION[1]:
        return UAT_A_PRESENTATION[0]
    if model_id == UAT_B_PRESENTATION[1]:
        return UAT_B_PRESENTATION[0]
    raise SeedError(f"未知の presentation モデル: {model_id}")


# --------------------------------------------------------------------------- #
# DB 反映
# --------------------------------------------------------------------------- #
async def _validate_model_enabled(session: Any, provider: str, model_id: str) -> None:
    from sqlalchemy import text

    row = (
        await session.execute(
            text("SELECT provider FROM llm_models WHERE id = :id AND enabled = true"),
            {"id": model_id},
        )
    ).scalar_one_or_none()
    if row is None:
        raise SeedError(f"モデル {model_id} が llm_models に無い/無効(migration 0002 seed を確認)")
    if row != provider:
        raise SeedError(f"モデル {model_id} は provider {provider} ではない(実際: {row})")


async def _reset_reserved_only(session: Any) -> None:
    """予約済み UAT ユーザーだけを削除する(CASCADE で個人資産も消える)。他ユーザー不変。"""
    from alinea_api.services.user_service import purge_user
    from alinea_core.db.models import User
    from sqlalchemy import select

    for email in RESERVED_EMAILS:
        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is not None:
            await purge_user(session, str(user.id))


async def _seed_reserved_user(
    session: Any,
    *,
    email: str,
    display_name: str,
    presentation: tuple[str, str],
) -> str:
    """1 人の予約済み UAT ユーザーを初期化して user_id を返す。"""
    from alinea_api.services.user_service import upsert_user_by_email
    from sqlalchemy import text

    provider, model_id = presentation
    await _validate_model_enabled(session, provider, model_id)

    user = await upsert_user_by_email(session, email, provider="email", display_name=display_name)
    user_id = str(user.id)

    # settings(presentation ルート + code_analysis 予算)。
    user.settings = build_uat_settings(model_id)
    session.add(user)

    # 実行時ルート = user_task_model_overrides を正とするため presentation を upsert する。
    await session.execute(
        text(
            "INSERT INTO user_task_model_overrides (user_id, task, model_id) "
            "VALUES (CAST(:u AS uuid), :t, :m) "
            "ON CONFLICT (user_id, task) DO UPDATE "
            "SET model_id = EXCLUDED.model_id, updated_at = now()"
        ),
        {"u": user_id, "t": PRESENTATION_TASK, "m": model_id},
    )
    await session.commit()
    return user_id


async def _issue_login_token(user_email: str) -> tuple[str, str]:
    """ワンタイムのメールリンクトークン(= one-time password)と検証 URL を返す。

    session_service を使い Redis に単回消費トークンを置く(15 分有効)。トークンは
    出力ファイルへだけ書き、ログ・stdout・repo へは残さない。
    """
    import redis.asyncio as redis
    from alinea_api.services import session_service
    from alinea_api.settings import get_api_settings

    settings = get_api_settings()
    client: Any = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        token = await session_service.create_email_link_token(client, user_email, "/dashboard")
    finally:
        await client.aclose()
    base = settings.app_base_url.rstrip("/")
    verify_url = f"{base}/api/auth/email/verify?token={token}"
    return token, verify_url


# --------------------------------------------------------------------------- #
# 出力(mode 0600)
# --------------------------------------------------------------------------- #
def write_accounts_file(path: Path, payload: dict[str, Any]) -> None:
    """アカウント情報を mode 0600 の JSON で書く(トークンを含むため厳格権限)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    # 既存ファイルの緩い権限を引き継がないよう、作成前に open flags で 0600 を確定する。
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    finally:
        # 既存ファイルを上書きした場合に備えて明示的に 0600 へ。
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


# --------------------------------------------------------------------------- #
# メイン
# --------------------------------------------------------------------------- #
async def seed_user_acceptance(*, reset: bool, output: Path) -> dict[str, Any]:
    """UAT アカウントを初期化し、出力ペイロード(ファイルへ書いた内容)を返す。"""
    from alinea_core.settings import get_settings
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    _require_review_env()
    fixtures = load_fixtures()

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            if reset:
                await _reset_reserved_only(session)
                await session.commit()
            uat_a_id = await _seed_reserved_user(
                session,
                email=UAT_A_EMAIL,
                display_name="UAT-A",
                presentation=UAT_A_PRESENTATION,
            )
            uat_b_id = await _seed_reserved_user(
                session,
                email=UAT_B_EMAIL,
                display_name="UAT-B",
                presentation=UAT_B_PRESENTATION,
            )
    finally:
        await engine.dispose()

    a_token, a_url = await _issue_login_token(UAT_A_EMAIL)
    b_token, b_url = await _issue_login_token(UAT_B_EMAIL)

    payload = {
        "generated_for": "user-acceptance-testing",
        "app_env": REVIEW_APP_ENV,
        "fixtures_schema_version": fixtures["schema_version"],
        "fixtures_verified_on": fixtures.get("verified_on"),
        "note": (
            "one_time_login_url は 15 分有効・単回消費。開くとセッションが張られます。"
            " このファイルは mode 0600・ログ非出力。外部論文はチェックリストの固定 URL を手入力。"
        ),
        "accounts": [
            {
                "label": "UAT-A",
                "user_id": uat_a_id,
                "email": UAT_A_EMAIL,
                "login_url": a_url,
                "one_time_password": a_token,
                "presentation_route": {
                    "provider": UAT_A_PRESENTATION[0],
                    "model": UAT_A_PRESENTATION[1],
                },
                "code_analysis_monthly_budget_usd": CODE_ANALYSIS_BUDGET_USD,
            },
            {
                "label": "UAT-B",
                "user_id": uat_b_id,
                "email": UAT_B_EMAIL,
                "login_url": b_url,
                "one_time_password": b_token,
                "presentation_route": {
                    "provider": UAT_B_PRESENTATION[0],
                    "model": UAT_B_PRESENTATION[1],
                },
                "code_analysis_monthly_budget_usd": CODE_ANALYSIS_BUDGET_USD,
            },
        ],
    }
    write_accounts_file(output, payload)
    return payload


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="seed_user_acceptance",
        description="review 環境で UAT-A / UAT-B を初期化する(Task 32)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="予約済み UAT アカウントのみ削除して再初期化する(他ユーザー不変)",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="アカウント情報の出力先(mode 0600 の JSON)",
    )
    args = parser.parse_args(argv)
    try:
        asyncio.run(seed_user_acceptance(reset=args.reset, output=args.output))
    except SeedError as exc:
        raise SystemExit(f"[seed_user_acceptance] {exc}") from exc
    # トークンは stdout へ出さない。出力ファイルの場所だけ知らせる。
    print(f"[seed_user_acceptance] 完了。アカウント情報(mode 0600)を書きました: {args.output}")


if __name__ == "__main__":
    main()
