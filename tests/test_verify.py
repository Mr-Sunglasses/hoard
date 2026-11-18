import hashlib

from app import verify


def test_git_blob_hash_matches_git(tmp_path):
    # `printf 'hello\n' | git hash-object --stdin`
    file = tmp_path / "hello.txt"
    file.write_bytes(b"hello\n")
    run = {"checked_bytes": 0}
    assert verify._git_blob_sha1(file, 6, run) == "ce013625030ba8dba906f756967f9e9ca394464a"
    assert run["checked_bytes"] == 6


def test_sha256(tmp_path):
    file = tmp_path / "w.bin"
    file.write_bytes(b"\x00" * 1000)
    assert verify._sha256(file, {"checked_bytes": 0}) == hashlib.sha256(b"\x00" * 1000).hexdigest()
