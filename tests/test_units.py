"""systemd units: every product service runs inside the capped slice."""
import configparser
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parents[1] / "ops"
SLICE = "game-of-the-day.slice"


def service_section(path):
    lines, inside = [], False
    for line in path.read_text().splitlines():
        if line.startswith("["):
            inside = line.strip() == "[Service]"
        elif inside and "=" in line and not line.lstrip().startswith("#"):
            lines.append(line)
    return dict(l.split("=", 1) for l in lines)


class Limits(unittest.TestCase):
    def test_every_service_is_in_the_slice(self):
        services = sorted(OPS.rglob("*.service"))
        self.assertGreater(len(services), 10)
        for path in services:
            with self.subTest(unit=path.name):
                self.assertEqual(service_section(path).get("Slice"), SLICE)

    def test_slice_caps_memory_and_cpu(self):
        cfg = configparser.ConfigParser(strict=False)
        cfg.optionxform = str
        cfg.read(OPS / SLICE)
        self.assertIn("MemoryMax", cfg["Slice"])
        self.assertIn("CPUQuota", cfg["Slice"])

    def test_model_runs_have_their_own_ceiling(self):
        for name in ("game-preparation.service", "game-reserve.service", "game-feedback-insights.service"):
            with self.subTest(unit=name):
                self.assertIn("MemoryMax", service_section(next(OPS.rglob(name))))

    def test_deploy_installs_slices(self):
        self.assertIn("'*.slice'", (OPS / "deploy.sh").read_text())


if __name__ == "__main__":
    unittest.main()
