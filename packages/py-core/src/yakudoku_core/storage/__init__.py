"""S3 互換オブジェクトストレージ(dev=MinIO / prod=R2)。"""

from yakudoku_core.storage.s3 import S3Storage, StorageKeys

__all__ = ["S3Storage", "StorageKeys"]
