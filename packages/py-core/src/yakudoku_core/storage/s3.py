"""aioboto3 による S3 互換ストレージのラッパとキー設計。

バケットは 2 つ(plans/00 §3 決定):
- sources: SourceAsset 原本(LaTeX tar・PDF・HTML・metadata)。
  Paper 削除時以外は再処理原資として保持。
- assets: 派生物(図・サムネ・概要図 SVG・解説図ラスター・エクスポート)。再生成可。
キー設計は plans/01 §7.1 に準拠。全オブジェクト非公開。配信は API の署名付き URL(§7.3)。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import aioboto3
from botocore.config import Config

from yakudoku_core.settings import CoreSettings, get_settings


class StorageKeys:
    """S3 キー生成(plans/01 §7.1)。ID はすべて UUID/ULID 文字列。"""

    # --- sources バケット ---
    @staticmethod
    def latex_tar(paper_id: str, source_version: str) -> str:
        return f"sources/{paper_id}/{source_version}/latex.tar.gz"

    @staticmethod
    def arxiv_html(paper_id: str, source_version: str) -> str:
        return f"sources/{paper_id}/{source_version}/arxiv.html"

    @staticmethod
    def original_pdf(paper_id: str, source_version: str) -> str:
        return f"sources/{paper_id}/{source_version}/original.pdf"

    @staticmethod
    def translated_pdf(paper_id: str, source_version: str, style: str) -> str:
        return f"sources/{paper_id}/{source_version}/translated-{style}.pdf"

    @staticmethod
    def bilingual_pdf(paper_id: str, source_version: str, style: str) -> str:
        return f"sources/{paper_id}/{source_version}/bilingual-{style}.pdf"

    @staticmethod
    def metadata(paper_id: str, source_version: str) -> str:
        return f"sources/{paper_id}/{source_version}/metadata.json"

    # --- assets バケット ---
    @staticmethod
    def figure(paper_id: str, revision_id: str, block_id: str, ext: str = "png") -> str:
        return f"figures/{paper_id}/{revision_id}/{block_id}.{ext}"

    @staticmethod
    def thumbnail(paper_id: str, retina: bool = False) -> str:
        return f"thumbnails/{paper_id}/card{'@2x' if retina else ''}.webp"

    @staticmethod
    def overview_svg(article_id: str, version: int) -> str:
        return f"renders/overview/{article_id}/v{version}.svg"

    @staticmethod
    def explainer_png(explainer_figure_id: str, version: int) -> str:
        return f"renders/explainer/{explainer_figure_id}/v{version}.png"

    @staticmethod
    def export(user_id: str, export_id: str) -> str:
        return f"exports/{user_id}/{export_id}.zip"


class S3Storage:
    """async S3 クライアント。sources / assets の 2 バケットを扱う。"""

    def __init__(self, settings: CoreSettings | None = None) -> None:
        self._s = settings or get_settings()
        self._session = aioboto3.Session()

    def _client_ctx(self, *, public: bool = False) -> Any:
        endpoint = self._s.s3_public_endpoint_url if public else self._s.s3_endpoint_url
        return self._session.client(
            "s3",
            endpoint_url=endpoint,
            region_name=self._s.s3_region,
            aws_access_key_id=self._s.s3_access_key_id,
            aws_secret_access_key=self._s.s3_secret_access_key,
            config=Config(signature_version="s3v4"),
        )

    async def put(
        self,
        bucket: str,
        key: str,
        body: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> None:
        async with self._client_ctx() as client:
            await client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                Metadata=metadata or {},
            )

    async def get(self, bucket: str, key: str) -> bytes:
        async with self._client_ctx() as client:
            resp = await client.get_object(Bucket=bucket, Key=key)
            async with resp["Body"] as stream:
                data: bytes = await stream.read()
                return data

    async def delete_many(self, bucket: str, keys: Iterable[str]) -> None:
        unique_keys = [key for key in dict.fromkeys(keys) if key]
        if not unique_keys:
            return
        async with self._client_ctx() as client:
            for i in range(0, len(unique_keys), 1000):
                await client.delete_objects(
                    Bucket=bucket,
                    Delete={
                        "Objects": [{"Key": key} for key in unique_keys[i : i + 1000]],
                        "Quiet": True,
                    },
                )

    async def presign_get(self, bucket: str, key: str, expires_in: int = 600) -> str:
        """署名付き GET URL を発行する(既定 600 秒。plans/03 §22.1)。

        ブラウザ到達用に public endpoint で署名する(コンテナ内 endpoint と分離。§7.3)。
        """
        async with self._client_ctx(public=True) as client:
            url: str = await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires_in,
            )
            return url

    @property
    def sources_bucket(self) -> str:
        return self._s.s3_bucket_sources

    @property
    def assets_bucket(self) -> str:
        return self._s.s3_bucket_assets
