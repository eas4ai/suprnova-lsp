"""Prove that diagnostic compiler caches are private and still execute real Cargo."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rsp_cache_test", ROOT / "tools/responsiveness-cargo-cache.py")
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class CompilerCacheSafety(unittest.TestCase):
    def test_copy_is_private_and_seed_unchanged_when_worker_artifacts_change(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source, target = root / "seed", root / "worker/cargo"
            source.mkdir()
            (source / "artifact").write_bytes(b"genuine cached artifact")
            before = cache.CompilerCache.inventory(source)
            self.assertTrue(cache.CompilerCache.seed(source, target, root))
            self.assertEqual(cache.CompilerCache.inventory(target), before)
            self.assertNotEqual((source / "artifact").stat().st_ino, (target / "artifact").stat().st_ino)
            (target / "artifact").write_bytes(b"new compiler artifact")
            self.assertEqual(cache.CompilerCache.inventory(source), before)
            self.assertFalse(cache.CompilerCache.seed(source, target, root))
            self.assertEqual((target / "artifact").read_bytes(), b"new compiler artifact")

    def test_refuses_external_overlapping_nonempty_or_linked_cache(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "seed"
            source.mkdir()
            (source / "artifact").write_bytes(b"cached")
            for target in (root.parent / "external", source, source / "nested"):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    cache.CompilerCache.seed(source, target, root)
            target = root / "worker/cargo"
            target.mkdir(parents=True)
            (target / "preserve").write_text("existing")
            with self.assertRaisesRegex(ValueError, "nonempty"):
                cache.CompilerCache.seed(source, target, root)
            self.assertEqual((target / "preserve").read_text(), "existing")
            (source / "link").symlink_to(root.parent)
            with self.assertRaisesRegex(ValueError, "symbolic"):
                cache.CompilerCache.seed(source, root / "other/cargo", root)

    def test_wrong_seed_marker_cannot_reseed_existing_worker(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "seed"
            source.mkdir()
            (source / "artifact").write_bytes(b"cached")
            target = root / "worker/cargo"
            cache.CompilerCache.seed(source, target, root)
            (target.parent / "rsp-cache-seeded.json").write_text(json.dumps({"seed": "other"}))
            with self.assertRaisesRegex(ValueError, "different source"):
                cache.CompilerCache.seed(source, target, root)

    def test_shim_preserves_real_cargo_argv_and_only_seeds_owned_worker_directories(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = root / "config.json"
            events = root / "events.jsonl"
            config.write_text(json.dumps({"seed": str(root / "seed"), "cargo": "/real/cargo", "events": str(events)}))
            args = ["rustdoc", "--locked", "--target", "x86_64-unknown-linux-gnu", "--lib", "--", "-Z", "unstable-options"]
            for directory, expected in (("worker/.suprnova-lsp-rustdoc-owned/cargo", True), ("ordinary/cargo", False)):
                target = root / directory
                with self.subTest(directory=directory), patch.dict(os.environ, {
                    "RSP_COMPILER_CACHE_CONFIG": str(config), "CARGO_TARGET_DIR": str(target)}), \
                    patch.object(cache.sys, "argv", ["cargo", *args]), \
                    patch.object(cache.CompilerCache, "seed", return_value=True) as seed, \
                    patch.object(cache.os, "execv") as execute:
                    cache.CompilerCache.run()
                    self.assertEqual(seed.called, expected)
                    execute.assert_called_once_with("/real/cargo", ["cargo", *args])
            row = json.loads(events.read_text())
            self.assertEqual(row["args"], args)
            self.assertTrue(row["seeded"])
            self.assertGreaterEqual(row["seedDurationNs"], 0)


if __name__ == "__main__":
    unittest.main()
