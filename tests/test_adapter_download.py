"""Offline regression coverage for atomic, bounded snapshot downloads."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier
from urllib.error import HTTPError

import pytest

from headroom.ingest.adapters import common

URL = "https://example.test/snapshot.csv"
OLD = b"previous complete snapshot"


class Response(BytesIO):
    def __init__(self, body=b"new snapshot", headers=None):
        super().__init__(body)
        self.headers = headers or {}


@pytest.fixture
def dest(tmp_path):
    path = tmp_path / "snapshot.csv"
    path.write_bytes(OLD)
    return path


def assert_preserved(dest):
    assert dest.read_bytes() == OLD
    assert list(dest.parent.iterdir()) == [dest]


def test_success_replaces_only_after_flush_and_sync(monkeypatch, dest):
    body = b"new complete snapshot"
    response = Response(body, {"Content-Length": str(len(body))})
    calls = []

    def open_response(request, *, timeout):
        assert request.full_url == URL
        assert request.get_header("User-agent") == common.UA
        assert timeout == 17
        return response

    def fsync(fd):
        assert dest.read_bytes() == OLD
        temporary, = dest.parent.glob(".*.part")
        assert temporary.read_bytes() == body
        calls.append("sync")

    monkeypatch.setattr(common.urllib.request, "urlopen", open_response)
    monkeypatch.setattr(common.os, "fsync", fsync)

    assert common.download(URL, dest, timeout=17) == dest
    assert dest.read_bytes() == body
    assert calls == ["sync"]
    assert response.closed
    assert list(dest.parent.iterdir()) == [dest]


def test_success_creates_missing_parent(monkeypatch, tmp_path):
    dest = tmp_path / "raw" / "snapshot.csv"
    monkeypatch.setattr(common.urllib.request, "urlopen", lambda *a, **kw: Response())
    common.download(URL, dest)
    assert dest.read_bytes() == b"new snapshot"
    assert list(dest.parent.iterdir()) == [dest]


@pytest.mark.parametrize("error", [TimeoutError("timed out"), OSError("read failed")])
@pytest.mark.parametrize("existing", [True, False])
def test_interrupted_stream_leaves_no_partial_file(monkeypatch, dest, error, existing):
    if not existing:
        dest.unlink()
    response = Response(b"partial transfer")

    def read(size):
        if response.tell():
            raise error
        return BytesIO.read(response, size)

    monkeypatch.setattr(response, "read", read)
    monkeypatch.setattr(common.urllib.request, "urlopen", lambda *a, **kw: response)

    with pytest.raises(type(error), match=str(error)):
        common.download(URL, dest)
    if existing:
        assert_preserved(dest)
    else:
        assert not list(dest.parent.iterdir())
    assert response.closed


def test_http_error_preserves_destination(monkeypatch, dest):
    def fail(*args, **kwargs):
        raise HTTPError(URL, 503, "Unavailable", {}, None)

    monkeypatch.setattr(common.urllib.request, "urlopen", fail)
    with pytest.raises(HTTPError):
        common.download(URL, dest)
    assert_preserved(dest)


@pytest.mark.parametrize("declared", [False, True])
def test_size_cap_preserves_destination(monkeypatch, dest, declared):
    body = b"x" * ((1 << 20) + 1)
    headers = {"Content-Length": str(len(body))} if declared else {}
    response = Response(body, headers)
    monkeypatch.setattr(common.urllib.request, "urlopen", lambda *a, **kw: response)
    with pytest.raises(RuntimeError, match="exceeded 1 MB cap"):
        common.download(URL, dest, max_mb=1)
    assert_preserved(dest)


def test_truncated_declared_transfer_preserves_destination(monkeypatch, dest):
    response = Response(b"partial", {"Content-Length": "20"})
    monkeypatch.setattr(common.urllib.request, "urlopen", lambda *a, **kw: response)
    with pytest.raises(RuntimeError, match="returned 7 bytes; expected 20"):
        common.download(URL, dest)
    assert_preserved(dest)


@pytest.mark.parametrize("headers", [
    {},
    {"Content-Length": "invalid"},
    {"Content-Length": "-1"},
    {"Content-Length": "999999999", "Transfer-Encoding": "chunked"},
])
def test_unknown_length_and_chunked_transfers(monkeypatch, dest, headers):
    monkeypatch.setattr(common.urllib.request, "urlopen", lambda *a, **kw: Response(headers=headers))
    common.download(URL, dest)
    assert dest.read_bytes() == b"new snapshot"
    assert list(dest.parent.iterdir()) == [dest]


def test_replacement_failure_preserves_destination(monkeypatch, dest):
    monkeypatch.setattr(common.urllib.request, "urlopen", lambda *a, **kw: Response())

    def fail_replace(self, target):
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        common.download(URL, dest)
    assert_preserved(dest)


def test_sync_failure_preserves_destination(monkeypatch, dest):
    monkeypatch.setattr(common.urllib.request, "urlopen", lambda *a, **kw: Response())

    def fail_sync(fd):
        raise OSError("sync failed")

    monkeypatch.setattr(common.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="sync failed"):
        common.download(URL, dest)
    assert_preserved(dest)


def test_concurrent_downloads_use_distinct_temporary_files(monkeypatch, dest):
    temporary_paths = []

    def capture_temporary_paths():
        temporary_paths.extend(dest.parent.glob(".*.part"))
        assert dest.read_bytes() == OLD

    barrier = Barrier(2, action=capture_temporary_paths)

    class ConcurrentResponse(Response):
        def read(self, size):
            if self.tell() == 0:
                barrier.wait(timeout=10)
            return super().read(size)

    def open_response(request, **kwargs):
        return ConcurrentResponse(request.full_url.encode())

    monkeypatch.setattr(common.urllib.request, "urlopen", open_response)
    urls = [URL + "?first", URL + "?second"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(common.download, url, dest) for url in urls]
        assert [future.result(timeout=15) for future in futures] == [dest, dest]

    assert len(set(temporary_paths)) == 2
    assert dest.read_bytes() in [url.encode() for url in urls]
    assert list(dest.parent.iterdir()) == [dest]
