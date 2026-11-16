"""CI routing regression without application imports or infrastructure."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "ci_scope.py"
spec = importlib.util.spec_from_file_location("ci_scope", SCRIPT)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class ScopeTests(unittest.TestCase):
    def test_fiscal_only(self):
        self.assertEqual(
            ci.classify(["packages/dfe/src/plugin.py", "packages/dfe/tests/test_xml.py"]),
            {"core": False, "iic": False, "fisc": True},
        )

    def test_iic_only(self):
        self.assertEqual(
            ci.classify(["iic_copilot/main.py", "frontend/src/App.tsx"]),
            {"core": False, "iic": True, "fisc": False},
        )

    def test_shared_and_unknown_run_everything(self):
        for path in [
            "packages/core/src/app/a.py",
            "packages/core-ui/src/a.tsx",
            "uv.lock",
            "package-lock.json",
            "architecture/boundaries.toml",
            "new-runtime.conf",
            "data/sample_docs/corpus.md",
            "packages/core/src/app/prompt.md",
            "packages/dfe/pyproject.toml",
            "packages/dfe/frontend/package.json",
            "frontend/package.json",
            ".github/workflows/dfe.yml",
        ]:
            with self.subTest(path=path):
                self.assertEqual(ci.classify([path]), ci.ALL)

    def test_documentation_only(self):
        self.assertFalse(
            any(ci.classify(["README.md", "docs/A.md", "packages/dfe/docs/A.md"]).values())
        )

    def test_mixed_products(self):
        self.assertEqual(
            ci.classify(["iic_copilot/main.py", "packages/dfe/src/main.py"]),
            {"core": False, "iic": True, "fisc": True},
        )

    def test_manual_schedule_and_missing_base(self):
        for event in ["workflow_dispatch", "schedule", "push"]:
            self.assertEqual(ci.select({}, event, "a" * 40), ci.ALL)
        self.assertEqual(ci.select({"before": "0" * 40}, "push", "a" * 40), ci.ALL)

    def test_unavailable_git_base(self):
        with patch.object(ci, "git", side_effect=subprocess.CalledProcessError(1, "git")):
            self.assertEqual(ci.select({"before": "b" * 40}, "push", "a" * 40), ci.ALL)

    def test_actual_git_diff_includes_deleted_and_renamed_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

            git("init", "-q")
            git("config", "user.email", "ci@example.invalid")
            git("config", "user.name", "CI test")
            fiscal = root / "packages/dfe/src"
            fiscal.mkdir(parents=True)
            (fiscal / "tool.py").write_text("sample")
            git("add", ".")
            git("commit", "-qm", "before")
            base = git("rev-parse", "HEAD")
            (root / "iic_copilot").mkdir()
            (fiscal / "tool.py").rename(root / "iic_copilot/tool.py")
            git("add", "-A")
            git("commit", "-qm", "move between products")
            head = git("rev-parse", "HEAD")
            with patch.object(ci, "git", side_effect=git):
                self.assertEqual(
                    ci.select({"before": base}, "push", head),
                    {"core": False, "iic": True, "fisc": True},
                )
                event = {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
                self.assertEqual(
                    ci.select(event, "pull_request", head),
                    {"core": False, "iic": True, "fisc": True},
                )


if __name__ == "__main__":
    unittest.main()
