import hashlib

import pytest

from storage import LocalDocumentStorage, document_storage_key


def test_local_storage_upload_download_and_idempotent_delete(tmp_path):
    storage = LocalDocumentStorage(tmp_path / "private")
    source = tmp_path / "staged.pdf"
    contents = b"%PDF-1.4 test document"
    source.write_bytes(contents)
    key = document_storage_key("workspace-1", "document-1", 1)
    checksum = hashlib.sha256(contents).hexdigest()

    assert storage.put_file(key, source, checksum) == len(contents)
    assert storage.exists(key)

    downloaded = tmp_path / "downloaded.pdf"
    storage.download_to_file(key, downloaded)
    assert downloaded.read_bytes() == contents

    storage.delete(key)
    storage.delete(key)
    assert not storage.exists(key)


def test_local_storage_rejects_invalid_keys_and_checksum_mismatch(tmp_path):
    storage = LocalDocumentStorage(tmp_path / "private")
    source = tmp_path / "staged.pdf"
    source.write_bytes(b"content")

    with pytest.raises(ValueError, match="Invalid document storage key"):
        storage.put_file("../outside.pdf", source, hashlib.sha256(b"content").hexdigest())
    with pytest.raises(IOError, match="checksum"):
        storage.put_file("workspaces/ws/docs/doc/file.pdf", source, "not-the-checksum")
    assert not (tmp_path / "outside.pdf").exists()


def test_storage_keys_do_not_include_user_filenames():
    key = document_storage_key("workspace-1", "document-1", 3)

    assert key == "workspaces/workspace-1/documents/document-1/versions/3/original.pdf"
    assert ".." not in key


def test_document_storage_key_rejects_path_separators():
    with pytest.raises(ValueError, match="workspace"):
        document_storage_key("../other", "document-1", 1)
