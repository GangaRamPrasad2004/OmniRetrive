"""Private storage adapters for original uploaded documents."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import BinaryIO, Protocol


class DocumentStorage(Protocol):
    def put_file(self, key: str, source: Path, checksum: str) -> int: ...

    def download_to_file(self, key: str, destination: Path) -> None: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def open(self, key: str) -> BinaryIO: ...

    def create_download_url(self, key: str, expires_in: int) -> str | None: ...


def document_storage_key(workspace_id: str, document_id: str, version: int) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workspace_id):
        raise ValueError("Invalid workspace identifier for document storage.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", document_id):
        raise ValueError("Invalid document identifier for document storage.")
    if version < 1:
        raise ValueError("Document version must be positive.")
    return f"workspaces/{workspace_id}/documents/{document_id}/versions/{version}/original.pdf"


class LocalDocumentStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("Invalid document storage key.")
        return candidate

    def put_file(self, key: str, source: Path, checksum: str) -> int:
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.uploading")
        try:
            shutil.copyfile(source, temporary)
            if _sha256(temporary) != checksum:
                raise OSError("Stored document checksum verification failed.")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination.stat().st_size

    def download_to_file(self, key: str, destination: Path) -> None:
        source = self._resolve_existing_or_legacy_path(key)
        if not source.is_file():
            raise FileNotFoundError("Stored document does not exist.")
        shutil.copyfile(source, destination)

    def exists(self, key: str) -> bool:
        return self._resolve_existing_or_legacy_path(key).is_file()

    def delete(self, key: str) -> None:
        path = self._resolve_existing_or_legacy_path(key)
        path.unlink(missing_ok=True)

    def open(self, key: str) -> BinaryIO:
        return self._resolve_existing_or_legacy_path(key).open("rb")

    def create_download_url(self, key: str, expires_in: int) -> str | None:
        return None

    def _resolve_existing_or_legacy_path(self, key: str) -> Path:
        candidate = Path(key)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            if resolved != self.root and self.root not in resolved.parents:
                raise ValueError(
                    "Legacy document path is outside configured local storage."
                )
            return resolved
        return self._path(key)


class S3DocumentStorage:
    def __init__(
        self,
        bucket: str,
        region: str | None = None,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
    ):
        if not bucket:
            raise ValueError("STORAGE_BUCKET is required for S3 document storage.")
        import boto3

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            region_name=region or None,
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
        )

    def put_file(self, key: str, source: Path, checksum: str) -> int:
        self.client.upload_file(
            str(source),
            self.bucket,
            key,
            ExtraArgs={
                "ContentType": "application/pdf",
                "Metadata": {"sha256": checksum},
            },
        )
        metadata = self.client.head_object(Bucket=self.bucket, Key=key)
        size = int(metadata["ContentLength"])
        if (
            size != source.stat().st_size
            or metadata.get("Metadata", {}).get("sha256") != checksum
        ):
            self.delete(key)
            raise OSError("Stored document verification failed.")
        return size

    def download_to_file(self, key: str, destination: Path) -> None:
        self.client.download_file(self.bucket, key, str(destination))

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.NoSuchKey:
            return False
        except Exception as exc:
            response = getattr(exc, "response", {})
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return False
            raise
        return True

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def open(self, key: str) -> BinaryIO:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"]

    def create_download_url(self, key: str, expires_in: int) -> str | None:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )


def get_document_storage() -> DocumentStorage:
    backend = os.getenv("STORAGE_BACKEND", "local").strip().lower()
    if backend == "local":
        root = os.getenv("STORAGE_LOCAL_ROOT", os.getenv("UPLOADS_DIR", "uploads"))
        return LocalDocumentStorage(root)
    if backend == "s3":
        return S3DocumentStorage(
            bucket=os.getenv("STORAGE_BUCKET", ""),
            region=os.getenv("STORAGE_REGION"),
            endpoint_url=os.getenv("STORAGE_ENDPOINT_URL"),
            access_key=os.getenv("STORAGE_ACCESS_KEY_ID"),
            secret_key=os.getenv("STORAGE_SECRET_ACCESS_KEY"),
        )
    raise ValueError("STORAGE_BACKEND must be either 'local' or 's3'.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
