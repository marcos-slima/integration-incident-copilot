"""Fetch the versioned operations asset and verify it before extraction."""

import hashlib
import json
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

source = json.loads((Path(__file__).resolve().parents[1] / "shared-libraries.json").read_text())[
    "operations"
]
with tempfile.TemporaryDirectory() as scratch:
    archive = Path(scratch) / "operations.tar.gz"
    with urllib.request.urlopen(source["url"], timeout=60) as response:
        archive.write_bytes(response.read())
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == source["sha256"]
    with tarfile.open(archive) as contents:
        for member in contents.getmembers():
            assert member.isfile() and not PurePosixPath(member.name).is_absolute()
            assert ".." not in PurePosixPath(member.name).parts
        contents.extractall(Path(sys.argv[1]), filter="data")
print("Verified operations bundle", source["version"])
