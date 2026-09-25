"""Portable skill packaging and explicit invocation tests."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class SkillPackageTests(unittest.TestCase):
    def test_codex_metadata_inserts_the_explicit_skill_invocation(self):
        metadata = yaml.safe_load((ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8"))
        self.assertIn("$decision-system-forge", metadata["interface"]["default_prompt"])
        self.assertTrue(metadata["policy"]["allow_implicit_invocation"])

    def test_skill_owns_direct_acquisition_and_system2_proposal_loop(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--repo https://github.com/example/operations", skill)
        self.assertIn("--url https://example.com/operations-policy.md", skill)
        self.assertIn("--proposal ./.forge/company-workflow/work_system_proposal.yaml", skill)
        self.assertIn("Do not ask the user to build a manifest", skill)
        self.assertIn("inside one skill invocation", skill)

    def test_skill_exposes_non_executing_integration_package_boundary(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("prepare-integration", skill)
        self.assertIn("integration packages", skill)
        self.assertIn("Bindings derived", skill)
        self.assertIn("remain stubs with no authority", skill)
        self.assertIn("Do not implement", skill)

    def test_installer_exposes_codex_and_claude_invocations_from_this_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".codex").mkdir()
            (home / ".claude").mkdir()
            environment = {**os.environ, "HOME": str(home)}

            installed = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install.sh")],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual((home / ".codex" / "skills" / "decision-system-forge").resolve(), ROOT)
            self.assertEqual((home / ".claude" / "skills" / "decision-system-forge").resolve(), ROOT)
            self.assertIn("$decision-system-forge", installed.stdout)
            self.assertIn("/decision-system-forge", installed.stdout)

            checked = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install.sh"), "--check"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn("Codex CLI", checked.stdout)
            self.assertIn("Claude Code", checked.stdout)
            self.assertIn("ready", checked.stdout)


if __name__ == "__main__":
    unittest.main()
