from __future__ import annotations

import re
import unittest
from pathlib import Path

from melvan import __version__


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class VersionTestCase(unittest.TestCase):
    def test_package_version_matches_project_metadata(self) -> None:
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version = "([^"]+)"$', pyproject, flags=re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), __version__)


if __name__ == "__main__":
    unittest.main()
