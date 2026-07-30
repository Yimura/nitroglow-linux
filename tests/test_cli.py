import os
import tempfile
import unittest

from nitroglow import cli, probe


class TestCLIParsing(unittest.TestCase):
    def test_probe_subcommand(self):
        args = cli.parse_args(["probe"])
        self.assertEqual(args.command, "probe")

    def test_set_parses_hex_colour(self):
        args = cli.parse_args(["set", "--color", "ff8000"])
        self.assertEqual(args.color, (0xFF, 0x80, 0x00))

    def test_set_rejects_bad_colour(self):
        with self.assertRaises(SystemExit):
            cli.parse_args(["set", "--color", "nothex"])

    def test_set_parses_mode_and_brightness(self):
        args = cli.parse_args(["set", "--mode", "custom", "--brightness", "80"])
        self.assertEqual(args.mode, "custom")
        self.assertEqual(args.brightness, 80)

    def test_dry_run_flag(self):
        self.assertTrue(cli.parse_args(["set", "--color", "010203",
                                        "--dry-run"]).dry_run)


class TestConfig(unittest.TestCase):
    def test_load_config_reads_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nitroglow.conf")
            with open(path, "w") as fh:
                fh.write("[glow]\nmode = custom\ncolor = 00ff00\nbrightness = 50\n")
            cfg = cli.load_config(path)
            self.assertEqual(cfg["mode"], "custom")
            self.assertEqual(cfg["color"], (0x00, 0xFF, 0x00))
            self.assertEqual(cfg["brightness"], 50)

    def test_missing_config_returns_empty(self):
        self.assertEqual(cli.load_config("/nonexistent/nitroglow.conf"), {})


class TestExitCodes(unittest.TestCase):
    def test_identity_error_when_sysfs_missing(self):
        # Top-level options must precede the subcommand.
        rc = cli.main(["--regs-path", "/nonexistent/amdgpu_regs",
                       "--sysfs-root", "/nonexistent", "probe"])
        self.assertEqual(rc, probe.EXIT_IDENTITY)


if __name__ == "__main__":
    unittest.main()
