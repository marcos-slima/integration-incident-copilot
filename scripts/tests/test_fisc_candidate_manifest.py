"""Reject incomplete candidates and validate delivery checksums offline."""

import importlib.util
import json
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "fisc_candidate_manifest.py"
spec = importlib.util.spec_from_file_location("fisc_manifest", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / "dist"
        self.out.mkdir()
        for relative in [
            "packages/dfe/pyproject.toml",
            "packages/core/pyproject.toml",
            "packages/core-ui/package.json",
            "packages/core/src/app/contracts/versioning.py",
            "uv.lock",
            "package-lock.json",
            "packages/dfe/data/eval/prompt_baseline.json",
            "packages/dfe/data/corpus/manifest.json",
            "packages/dfe/data/schemas/nfe/PL_010g_v1.03.manifest.json",
        ]:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((SCRIPT.parents[1] / relative).read_bytes())
        self.versions = {
            name: tomllib.loads((self.root / f"packages/{folder}/pyproject.toml").read_text())[
                "project"
            ]["version"]
            for name, folder in [("copilot-core", "core"), ("dfe-copilot", "dfe")]
        }
        for name, version in self.versions.items():
            self.wheel(name, version)

    def wheel(self, name, version):
        normalized = name.replace("-", "_")
        with zipfile.ZipFile(self.out / f"{normalized}-{version}-py3-none-any.whl", "w") as archive:
            archive.writestr(
                f"{normalized}-{version}.dist-info/METADATA", f"Name: {name}\nVersion: {version}\n"
            )

    def test_incomplete_candidate_rejected(self):
        next(self.out.glob("dfe_copilot*.whl")).unlink()
        with self.assertRaisesRegex(ValueError, "Both fiscal and core"):
            module.manifest(self.root, self.out)

    def test_wrong_version_rejected(self):
        next(self.out.glob("dfe_copilot*.whl")).unlink()
        self.wheel("dfe-copilot", "0.9.0")
        with self.assertRaisesRegex(ValueError, "Unexpected wheel/version"):
            module.manifest(self.root, self.out)

    def test_checksums_cover_wheels_and_manifest(self):
        with (
            patch.object(module, "ROOT", self.root),
            patch.object(module.sys, "argv", ["manifest", str(self.out)]),
            patch.object(module.subprocess, "check_output", return_value="a" * 40),
        ):
            module.main()
        manifest = json.loads((self.out / "candidate.json").read_text())
        self.assertEqual(manifest["status"], "candidate-not-a-release")
        self.assertEqual(manifest["versions"], self.versions)
        self.assertEqual(manifest["source_commit"], "a" * 40)
        sums = (self.out / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(sums), 3)
        for line in sums:
            digest, name = line.split("  ")
            self.assertEqual(module.sha256(self.out / name), digest)
        # Corruption after packaging must be observable by the recipient.
        wheel = next(self.out.glob("*.whl"))
        original = manifest["artifact_sha256"][wheel.name]
        wheel.write_bytes(b"changed")
        self.assertNotEqual(module.sha256(wheel), original)


if __name__ == "__main__":
    unittest.main()
