"""Command-line interface.

Guarantees, in order, for every command that touches hardware:
  identity check -> advisory lock -> save registers -> work -> restore,
with restore also wired to SIGINT/SIGTERM/SIGHUP.
"""

import argparse
import configparser
import fcntl
import os
import signal
import sys

from nitroglow import bitbang, glow, identity, pad as padmod, probe, regs, smbus

LOCK_PATH = "/run/lock/nitroglow.lock"
CONFIG_PATH = "/etc/nitroglow.conf"


def _parse_color(text):
    if len(text) != 6:
        raise argparse.ArgumentTypeError("colour must be 6 hex digits, e.g. ff8000")
    try:
        value = int(text, 16)
    except ValueError:
        raise argparse.ArgumentTypeError("colour must be hex, e.g. ff8000")
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="nitroglow",
                                     description="Sapphire Nitro Glow control")
    parser.add_argument("--regs-path", default=regs.DEBUGFS_REGS)
    parser.add_argument("--sysfs-root", default=identity.DEFAULT_SYSFS)
    parser.add_argument("--delay", type=float, default=1e-5,
                        help="per-half-bit delay in seconds")
    parser.add_argument("--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="test whether the MCU answers")
    p_probe.add_argument("--dry-run", action="store_true")

    p_get = sub.add_parser("get", help="read current mode, colour, brightness")
    p_get.add_argument("--dry-run", action="store_true")

    p_set = sub.add_parser("set", help="set mode, colour and/or brightness")
    p_set.add_argument("--mode", choices=sorted(glow.MODES))
    p_set.add_argument("--color", type=_parse_color)
    p_set.add_argument("--brightness", type=int)
    p_set.add_argument("--dry-run", action="store_true")

    p_off = sub.add_parser("off", help="turn the lighting off")
    p_off.add_argument("--dry-run", action="store_true")

    p_apply = sub.add_parser("apply", help="apply the config file")
    p_apply.add_argument("--config", default=CONFIG_PATH)
    p_apply.add_argument("--dry-run", action="store_true")

    p_restore = sub.add_parser("restore", help="restore saved register state")
    p_restore.add_argument("--dry-run", action="store_true")

    return parser.parse_args(argv)


def load_config(path):
    parser = configparser.ConfigParser()
    if not parser.read(path):
        return {}
    if "glow" not in parser:
        return {}
    section = parser["glow"]
    cfg = {}
    if "mode" in section:
        cfg["mode"] = section["mode"]
    if "color" in section:
        cfg["color"] = _parse_color(section["color"])
    if "brightness" in section:
        cfg["brightness"] = int(section["brightness"])
    return cfg


class _Session:
    """identity + lock + open + save/restore, as a context manager."""

    def __init__(self, args):
        self.args = args
        self.regfile = None
        self.pad = None
        self._lock_fd = None

    def __enter__(self):
        identity.check_identity(self.args.sysfs_root)
        self._lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.regfile = regs.RegFile(self.args.regs_path,
                                    dry_run=getattr(self.args, "dry_run", False))
        self.pad = padmod.Pad(self.regfile)
        self.pad.save()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, self._on_signal)
        self.pad.claim()
        return self

    def _on_signal(self, signum, frame):
        self._cleanup()
        sys.exit(128 + signum)

    def _cleanup(self):
        try:
            if self.pad is not None:
                self.pad.release()
                self.pad.restore()
        finally:
            if self.regfile is not None:
                self.regfile.close()
            if self._lock_fd is not None:
                os.close(self._lock_fd)

    def __exit__(self, *exc_info):
        self._cleanup()
        return False

    def glow(self):
        bb = bitbang.BitBang(self.pad, delay=self.args.delay)
        return glow.Glow(smbus.SMBus(bb))


def main(argv=None):
    args = parse_args(argv)

    if args.command == "probe" and args.dry_run:
        print("dry-run: would claim pads, clear MASK bit 12, read Y, "
              "then address 0x%02x" % glow.ADDR)
        return probe.EXIT_OK

    try:
        with _Session(args) as session:
            if args.command == "probe":
                result = probe.run_probe(session.pad, delay=args.delay)
                print(result.message)
                return result.code

            g = session.glow()

            if args.command == "get":
                print("mode=0x%02x color=%s brightness=0x%02x"
                      % (g.get_mode(), g.get_color(), g.get_brightness()))
                return 0

            if args.command == "set":
                if args.color is not None:
                    g.set_color(*args.color)
                if args.brightness is not None:
                    g.set_brightness(args.brightness)
                if args.mode is not None:
                    g.set_mode(glow.MODES[args.mode])
                return 0

            if args.command == "off":
                g.off()
                return 0

            if args.command == "apply":
                cfg = load_config(args.config)
                if not cfg:
                    print("no config at %s" % args.config, file=sys.stderr)
                    return 1
                if "color" in cfg:
                    g.set_color(*cfg["color"])
                if "brightness" in cfg:
                    g.set_brightness(cfg["brightness"])
                if "mode" in cfg:
                    g.set_mode(glow.MODES[cfg["mode"]])
                return 0

            if args.command == "restore":
                return 0        # _Session.__exit__ restores unconditionally

    except identity.IdentityError as exc:
        print("identity check failed: %s" % exc, file=sys.stderr)
        return probe.EXIT_IDENTITY
    except regs.RegError as exc:
        print("register access failed: %s" % exc, file=sys.stderr)
        return probe.EXIT_ACCESS
    except (BlockingIOError, PermissionError) as exc:
        print("cannot acquire %s: %s" % (LOCK_PATH, exc), file=sys.stderr)
        return probe.EXIT_ACCESS
    except (bitbang.BitBangError, glow.GlowError) as exc:
        print("bus error: %s" % exc, file=sys.stderr)
        return 1

    return 1
