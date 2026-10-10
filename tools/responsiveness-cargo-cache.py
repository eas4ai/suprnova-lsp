#!/usr/bin/env python3
"""Copy a disclosed diagnostic compiler cache before running the real Cargo command."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent.parent


class CompilerCache:
    @staticmethod
    def inventory(directory, contents=True):
        """Reject links and hash bounded regular artifacts without loading them into memory."""
        files, total = {}, 0
        for parent, children, names in os.walk(directory, followlinks=False):
            for name in [*children, *names]:
                if (Path(parent) / name).is_symlink():
                    raise ValueError("compiler seed contains a symbolic link")
            for name in names:
                path = Path(parent) / name
                if not path.is_file():
                    raise ValueError("compiler seed contains a nonregular artifact")
                total += path.stat().st_size
                if len(files) >= 100_000 or total > 64 * 1024**3:
                    raise ValueError("compiler seed exceeds its file or byte bound")
                key = str(path.relative_to(directory))
                if contents:
                    with path.open("rb") as stream:
                        files[key] = hashlib.file_digest(stream, "sha256").hexdigest()
                else:
                    files[key] = path.stat().st_size
        if not files:
            raise ValueError("compiler seed is empty")
        return files

    @staticmethod
    def seed(source, destination, owned_root):
        """Each worker gets independent files; Cargo must still validate and rebuild its outputs."""
        source, destination, owned_root = source.resolve(), destination.resolve(), owned_root.resolve()
        if not source.is_relative_to(owned_root) or not destination.is_relative_to(owned_root):
            raise ValueError("compiler cache escapes the owned artifact root")
        if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
            raise ValueError("compiler seed and destination overlap")
        marker = destination.parent / "rsp-cache-seeded.json"
        if marker.exists():
            if json.loads(marker.read_text()) != {"seed": str(source)}:
                raise ValueError("worker cache was seeded from a different source")
            return False
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("refusing to seed a nonempty worker cache")
        inventory = CompilerCache.inventory(source, contents=False)
        destination.mkdir(parents=True, exist_ok=True)
        # Reflinks share storage only until a write; no hard link joins mutable
        # worker outputs to the seed. The enclosing supervisor owns this process.
        subprocess.run(["cp", "-a", "--reflink=auto", str(source) + "/.", str(destination)],
                       check=True, timeout=120)
        if CompilerCache.inventory(destination, contents=False) != inventory:
            raise ValueError("private compiler cache copy differs from its seed")
        marker.write_text(json.dumps({"seed": str(source)}) + "\n")
        return True

    @classmethod
    def run(cls):
        config = json.loads(Path(os.environ["RSP_COMPILER_CACHE_CONFIG"]).read_text())
        args = sys.argv[1:]
        target = os.environ.get("CARGO_TARGET_DIR")
        if target and ".suprnova-lsp-rustdoc-" in Path(target).parent.name:
            begin = time.monotonic_ns()
            seeded = cls.seed(Path(config["seed"]), Path(target), ROOT / "target/agent-debug")
            event = {"args": args, "pid": os.getpid(), "writtenNs": begin,
                     "seeded": seeded, "seed": config["seed"], "target": target,
                     "seedDurationNs": time.monotonic_ns() - begin}
            with Path(config["events"]).open("a") as stream:
                stream.write(json.dumps(event) + "\n")
        # Preserve every production argument. In particular this neither returns
        # captured JSON nor reports compiler success without executing Cargo.
        os.execv(config["cargo"], ["cargo", *args])


if __name__ == "__main__":
    CompilerCache.run()
