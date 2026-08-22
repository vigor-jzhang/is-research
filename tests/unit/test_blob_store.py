import hashlib
import pathlib

import pytest

from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore


@pytest.mark.asyncio
async def test_blob_put_get(tmp_path: pathlib.Path):
    store = FilesystemBlobStore(root=tmp_path / "blobs")
    data = b"hello world pdf content"
    ref = await store.put_bytes(data, media_type="application/pdf")
    assert ref.digest == hashlib.sha256(data).hexdigest()
    assert ref.size_bytes == len(data)
    assert ref.storage_key.count("/") == 2
    fetched = await store.get_bytes(ref)
    assert fetched == data
    fetched2 = await store.get_bytes(ref.storage_key)
    assert fetched2 == data
    fetched3 = await store.get_bytes(ref.digest)
    assert fetched3 == data


@pytest.mark.asyncio
async def test_blob_same_bytes_same_digest(tmp_path: pathlib.Path):
    store = FilesystemBlobStore(root=tmp_path / "blobs")
    data = b"same bytes"
    r1 = await store.put_bytes(data)
    r2 = await store.put_bytes(data)
    assert r1.digest == r2.digest
    assert r1.storage_key == r2.storage_key
    # Filesystem should have only one file
    count = sum(1 for _ in (tmp_path / "blobs").rglob("*") if _.is_file())
    assert count == 1


@pytest.mark.asyncio
async def test_blob_atomic_persistence_and_reopen(tmp_path: pathlib.Path):
    root = tmp_path / "blobs"
    store = FilesystemBlobStore(root=root)
    data = b"persistent data"
    ref = await store.put_bytes(data)
    # New store instance on same root should see it
    store2 = FilesystemBlobStore(root=root)
    assert await store2.exists(ref)
    assert await store2.get_bytes(ref) == data
    stat = await store2.stat(ref)
    assert stat.exists is True
    assert stat.size_bytes == len(data)
    assert stat.digest == ref.digest


@pytest.mark.asyncio
async def test_blob_missing(tmp_path: pathlib.Path):
    store = FilesystemBlobStore(root=tmp_path / "blobs")
    fake_ref = "aa/bb/" + "a" * 64
    assert await store.exists(fake_ref) is False
    stat = await store.stat(fake_ref)
    assert stat.exists is False
    with pytest.raises(FileNotFoundError):
        await store.get_bytes(fake_ref)
    # Digest form
    assert await store.exists("b" * 64) is False


@pytest.mark.asyncio
async def test_blob_size_hash_metadata(tmp_path: pathlib.Path):
    store = FilesystemBlobStore(root=tmp_path / "blobs")
    data = b"x" * 1234
    ref = await store.put_bytes(data, media_type="text/plain")
    assert ref.size_bytes == 1234
    assert ref.media_type == "text/plain"
    stat = await store.stat(ref)
    assert stat.size_bytes == 1234
    assert stat.digest == hashlib.sha256(data).hexdigest()


@pytest.mark.asyncio
async def test_blob_arbitrary_filename_cannot_escape(tmp_path: pathlib.Path):
    store = FilesystemBlobStore(root=tmp_path / "blobs")
    # Attempt to use traversal key
    with pytest.raises(ValueError):
        await store.get_bytes("../../etc/passwd")
    with pytest.raises(ValueError):
        await store.get_bytes("/absolute/path")
    with pytest.raises(ValueError):
        await store.get_bytes("aa/bb/../../escape")
    # Put should also sanitize; try to ensure storage_key is not user-controlled filename
    data = b"safe"
    ref = await store.put_bytes(data)
    # storage_key must be hex only
    assert all(c in "0123456789abcdef/" for c in ref.storage_key)
    # No absolute path in ref
    assert not ref.storage_key.startswith("/")


@pytest.mark.asyncio
async def test_blob_delete(tmp_path: pathlib.Path):
    store = FilesystemBlobStore(root=tmp_path / "blobs")
    data = b"to delete"
    ref = await store.put_bytes(data)
    assert await store.exists(ref) is True
    await store.delete(ref)
    assert await store.exists(ref) is False
    assert await store.exists(ref.digest) is False
