"""Operational distribution must include migrations and exclude local/customer data."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "build_shared_distribution.py"
spec = importlib.util.spec_from_file_location("shared_build", SCRIPT)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        resources = {
            "alembic.ini": "[alembic]\nscript_location = %(here)s/alembic\n",
            "LICENSE": "fixture license",
            "alembic/env.py": "# env",
            "alembic/script.py.mako": "# template",
            "alembic/versions/001.py": 'revision = "001"',
            "scripts/validate_dashboards.py": "# validator",
            "deploy/grafana/dashboards/example.json": "{}",
            "packages/core/docs/OPERATIONS.md": "Operations fixture",
            ".env": "LOCAL_ONLY",
            "alembic/versions/.env": "LOCAL_ONLY",
            "packages/dfe/data/client.xml": "CUSTOMER_DATA",
        }
        for name, content in resources.items():
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        self.out = self.root / "out"
        self.out.mkdir()

    def test_allowlist_and_provenance(self):
        import tarfile

        archive = build.operations_bundle(self.root, self.out, "1.2.3", "a" * 40)
        with tarfile.open(archive) as contents:
            names = contents.getnames()
            self.assertIn("alembic/versions/001.py", names)
            self.assertIn("deploy/grafana/dashboards/example.json", names)
            self.assertFalse(any(".env" in name or "client.xml" in name for name in names))
            manifest = json.load(contents.extractfile("MANIFEST.json"))
        self.assertEqual(manifest["source_commit"], "a" * 40)
        self.assertEqual(manifest["core_version"], "1.2.3")
        self.assertEqual(
            manifest["files_sha256"]["alembic.ini"], build.digest(self.root / "alembic.ini")
        )

    def test_missing_revisions_rejected(self):
        (self.root / "alembic/versions/001.py").unlink()
        with self.assertRaisesRegex(ValueError, "require migration revisions"):
            build.operations_bundle(self.root, self.out, "1.2.3", "a" * 40)

    def test_symlink_resource_rejected(self):
        (self.root / "alembic/env.py").unlink()
        (self.root / "alembic/env.py").symlink_to(self.root / ".env")
        with self.assertRaisesRegex(ValueError, "Unexpected operational resource"):
            build.operations_bundle(self.root, self.out, "1.2.3", "a" * 40)


if __name__ == "__main__":
    unittest.main()
