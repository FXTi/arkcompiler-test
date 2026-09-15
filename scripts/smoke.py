#!/usr/bin/env python3
"""Consumer-side integration tests: offline image, export, oracle, failure paths."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image", default="arkcompiler-test")
    args = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="ark-consumer-") as tmp:
        root = Path(tmp)
        base = ["docker", "run", "--rm", "--network", "none", "--read-only",
                "--tmpfs", "/tmp", "--user", str(os.getuid()) + ":" + str(os.getgid()),
                "-v", tmp + ":/work", args.image]

        def run(*command, ok=True):
            r = subprocess.run(base + list(command), capture_output=True, text=True)
            if (r.returncode == 0) != ok:
                raise AssertionError(str(command) + "\n" + r.stdout + r.stderr)
            return json.loads(r.stdout) if r.stdout.strip() else None

        info = run("info")
        assert len(info["corpus"]["versions"]) == 6
        run("verify", "--execute")
        result = run("export", "/work/fixtures", "--case", "local/arithmetic", "--profile", "baseline")
        assert result["verified"] == 6
        run("verify", "--root", "/work/fixtures", "--execute")
        run("export", "/work/fixtures", ok=False)
        for version in info["corpus"]["versions"]:
            path = "/work/fixtures/" + version + "/local/arithmetic/baseline/input.abc"
            assert run("inspect", path)["version"] == version
            assert run("compare", path, "--case", "local/arithmetic", "--version", version)["matches"]
        (root / "different.js").write_text("print(43);\n")
        matrix = run("compile-matrix", "/work/different.js", "/work/matrix")
        assert len(matrix) == 6
        run("compile", "/work/different.js", "/work/different.abc", "--version", "24.0.0.0")
        run("compare", "/work/different.abc", "--case", "local/arithmetic", "--version", "24.0.0.0", ok=False)
        run("compare", "/work/different.abc", "--case", "local/arithmetic", "--version", "9.0.0.0", ok=False)
        (root / "loop.js").write_text("while (true) {}\n")
        run("compile", "/work/loop.js", "/work/loop.abc", "--version", "24.0.0.0")
        result = run("run", "/work/loop.abc", "--timeout", "0.2", ok=False)
        assert result["timeout"]
        (root / "broken.abc").write_bytes(b"PANDA")
        run("inspect", "/work/broken.abc", ok=False)
        first = root / "fixtures/24.0.0.0/local/arithmetic/baseline/input.abc"
        first.write_bytes(first.read_bytes()[:-1])
        run("verify", "--root", "/work/fixtures", ok=False)
        print(json.dumps({"status": "passed", "corpus": info["corpus"], "checks": ["offline", "read-only", "non-root", "six-version-export", "runtime-oracle", "mismatch", "timeout", "corruption"]}))


if __name__ == "__main__":
    main()
