#!/usr/bin/env python3
"""Black-box Ark fixture generator and oracle. Python standard library only."""
import argparse
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = Path(os.environ.get("ARK_TEST_ROOT", "/opt/arkcompiler-test"))


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inspect(path):
    """Only parse the stable ABC header, never use abcd-rs as its own oracle."""
    data = Path(path).read_bytes()
    if len(data) < 60 or data[:8] != b"PANDA\0\0\0":
        raise ValueError("invalid/truncated ABC header: " + str(path))
    checksum = struct.unpack_from("<I", data, 8)[0]
    size = struct.unpack_from("<I", data, 16)[0]
    if size != len(data):
        raise ValueError("ABC file_size mismatch: " + str(path))
    if checksum != zlib.adler32(data[12:]):
        raise ValueError("ABC checksum mismatch: " + str(path))
    return {"version": ".".join(map(str, data[12:16])), "file_size": size,
            "checksum": checksum, "sha256": digest(path)}


def call(tool, args, cwd=None, timeout=30):
    argv = [str(ROOT / "bin" / tool)] + list(map(str, args))
    env = dict(os.environ, LD_LIBRARY_PATH=str(ROOT / "lib"), LC_ALL="C.UTF-8", TZ="UTC")
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
        return {"command": argv, "exit_code": p.returncode,
                "stdout": p.stdout.decode("utf-8", errors="replace"),
                "stderr": p.stderr.decode("utf-8", errors="replace"), "timeout": False}
    except subprocess.TimeoutExpired as e:
        return {"command": argv, "exit_code": 124, "timeout": True,
                "stdout": (e.stdout or b"").decode("utf-8", errors="replace"),
                "stderr": (e.stderr or b"").decode("utf-8", errors="replace")}


def require_ok(result):
    if result["exit_code"] != 0 or result["timeout"]:
        raise RuntimeError(json.dumps(result, ensure_ascii=True))


def compile_raw(source, output, version, profile, mode="script", cwd=None, extra_args=None):
    """Invoke es2abc and return the raw result record without raising on failure."""
    versions = read_json(ROOT / "versions.json")
    profiles = read_json(ROOT / "profiles.json")
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("output already exists: " + str(output))
    args = versions[version] + profiles[profile]
    args += ["--extension=" + Path(source).suffix.lstrip(".")]
    if mode != "script":
        args += ["--" + mode]
    args += list(extra_args or [])
    return call("es2abc", args + ["--output=" + str(output), str(source)], cwd=cwd)


def compile_one(source, output, version, profile, mode="script", cwd=None, extra_args=None):
    result = compile_raw(source, output, version, profile, mode, cwd, extra_args)
    require_ok(result)
    result["abc"] = inspect(output)
    if result["abc"]["version"] != version:
        raise ValueError("compiler emitted wrong version: " + str(result))
    return result


def selected(rows, version=None, case=None, profile=None):
    return [r for r in rows if (version is None or r["version"] == version)
            and (case is None or r["case"] == case)
            and (profile is None or r["profile"] == profile)]


def test262_fixture(job, dest, rel, source):
    """Build one preprocessed test262 case. es2abc rejection is a counted
    bucket, not a build failure; runtime output is recorded raw."""
    case, v, profile = job["case"], job["version"], job["profile"]
    corpus = ROOT / "corpus"
    c = compile_raw(source, dest / "input.abc", v, profile,
                    case.get("mode", "script"), cwd=corpus,
                    extra_args=case.get("compile_args", []))
    row = {"schema_version": 1, "case": case["id"], "version": v,
           "profile": profile, "source": source, "tags": case["tags"],
           "origin": case["origin"], "test262": case["test262"],
           "source_sha256": digest(corpus / source), "compile": c}
    if c["exit_code"] != 0 or c["timeout"]:
        negative = case["test262"].get("negative") or {}
        if negative.get("phase") == "parse":
            declared = negative.get("type", "")
            match = re.search(r"\b[A-Za-z]*Error\b", c["stderr"])
            row["bucket"] = "expected-negative-parse"
            row["agreement"] = bool(match) and match.group(0) == declared
            row["declared_type"] = declared
            row["stderr_error_type"] = match.group(0) if match else None
        else:
            row["bucket"] = "es2abc-cant"
        shutil.rmtree(dest)
        return row
    c["abc"] = inspect(dest / "input.abc")
    if c["abc"]["version"] != v:
        raise ValueError("compiler emitted wrong version: " + str(c))
    d = call("ark_disasm", [dest / "input.abc", dest / "reference.pa"])
    require_ok(d)
    if not (dest / "reference.pa").stat().st_size:
        raise ValueError("empty disassembly: " + case["id"])
    r = call("ark_js_vm", [dest / "input.abc"], cwd=corpus)
    row.update({"bucket": "compiled", "abc": str(rel / "input.abc"),
                "pandasm": str(rel / "reference.pa"), "header": c["abc"],
                "pandasm_sha256": digest(dest / "reference.pa"),
                "disassemble": d, "runtime": {"status": "recorded", **r}})
    write_json(dest / "metadata.json", row)
    return row


def fixture_job(job):
    """Build one fixture in its own (version/case/profile) directory.

    Worker for the generate() pool: returns (index, row, None) on success or
    (index, None, error) on failure so the parent can report every failure.
    """
    case, v, profile = job["case"], job["version"], job["profile"]
    corpus = ROOT / "corpus"
    try:
        rel = Path(v) / case["id"] / profile
        dest = corpus / rel
        dest.mkdir(parents=True, exist_ok=True)
        source = "sources/" + case["source"]
        if case["origin"].get("kind") == "test262":
            return (job["index"], test262_fixture(job, dest, rel, source), None)
        c = compile_one(source, dest / "input.abc", v, profile,
                        case.get("mode", "script"), cwd=corpus,
                        extra_args=case.get("compile_args", []))
        d = call("ark_disasm", [dest / "input.abc", dest / "reference.pa"])
        require_ok(d)
        if not (dest / "reference.pa").stat().st_size:
            raise ValueError("empty disassembly: " + case["id"])
        row = {"schema_version": 1, "case": case["id"], "version": v,
               "profile": profile, "source": source, "tags": case["tags"],
               "origin": case["origin"], "abc": str(rel / "input.abc"),
               "pandasm": str(rel / "reference.pa"), "header": c["abc"],
               "source_sha256": digest(corpus / source),
               "pandasm_sha256": digest(dest / "reference.pa"),
               "compile": c, "disassemble": d,
               "runtime": {"status": "not-applicable", "reason": case.get("runtime_reason", "structural fixture")}}
        if "expected_stdout" in case:
            r = call("ark_js_vm", [dest / "input.abc"], cwd=corpus)
            require_ok(r)
            if r["stdout"] != case["expected_stdout"] or r["stderr"]:
                raise ValueError("runtime oracle mismatch " + str(rel) + ": " + str(r))
            row["runtime"] = {"status": "passed", **r}
        write_json(dest / "metadata.json", row)
        return (job["index"], row, None)
    except (ValueError, RuntimeError, OSError, KeyError) as e:
        return (job["index"], None, "%s (%s/%s/%s)" % (e, case["id"], v, profile))


def generate():
    versions = read_json(ROOT / "versions.json")
    profiles = read_json(ROOT / "profiles.json")
    cases = read_json(ROOT / "cases.json")
    corpus = ROOT / "corpus"
    if corpus.exists():
        raise ValueError("corpus already exists; use a clean staging tree")
    corpus.mkdir()
    if (ROOT / "pandasm").exists():
        shutil.copytree(ROOT / "pandasm", corpus / "pandasm")
    write_json(corpus / "build-info.json", read_json(ROOT / "build-info.json"))
    write_json(corpus / "versions.json", versions)
    write_json(corpus / "profiles.json", profiles)
    shutil.copytree(ROOT / "sources", corpus / "sources")
    # Confirm the command-line map AND each generated file's actual header.
    for v, flags in versions.items():
        r = call("es2abc", flags + ["--target-bc-version"])
        require_ok(r)
        if r["stdout"].strip() != v:
            raise ValueError("target-bc-version mismatch: " + str(r))
    jobs = []
    for case in cases:
        if case.get("skip"):
            continue  # counted below as a skipped-* bucket, never generated
        for v in case.get("versions", versions):
            for profile in case.get("profiles", profiles):
                jobs.append({"index": len(jobs), "case": case,
                             "version": v, "profile": profile})
    workers = int(os.environ.get("ARK_TEST_JOBS", "0")) or (os.cpu_count() or 1)
    rows = [None] * len(jobs)
    failures = []
    progress = [0]

    def collect(results):
        for index, row, error in results:
            progress[0] += 1
            if error is not None:
                failures.append((index, error))
            else:
                rows[index] = row
            if progress[0] % 200 == 0 or progress[0] == len(jobs):
                print("generated %d/%d fixtures" % (progress[0], len(jobs)), flush=True)

    if workers > 1 and len(jobs) > 1:
        with multiprocessing.Pool(workers) as pool:
            collect(pool.imap_unordered(fixture_job, jobs, chunksize=4))
    else:
        collect(map(fixture_job, jobs))
    if failures:
        for index, error in failures:
            print("fixture job %d failed: %s" % (index, error), file=sys.stderr)
        raise RuntimeError("%d of %d fixture jobs failed" % (len(failures), len(jobs)))
    # Row order follows job enumeration order, not completion order: the
    # index is byte-identical to a serial run.
    (corpus / "index.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    buckets = {}
    for row in rows:
        bucket = row.get("bucket", "compiled")
        buckets[bucket] = buckets.get(bucket, 0) + 1
    for case in cases:
        if case.get("skip"):
            bucket = "skipped-" + case["skip"]
            buckets[bucket] = buckets.get(bucket, 0) + 1
    write_json(corpus / "summary.json", {"schema_version": 1, "cases": len(cases),
               "fixtures": len(rows), "versions": list(versions),
               "runtime_checked": sum(r.get("runtime", {}).get("status") == "passed" for r in rows),
               "buckets": dict(sorted(buckets.items()))})


def index(root):
    return [json.loads(line) for line in (root / "index.jsonl").read_text().splitlines()]


def safe_path(root, rel):
    p = (root / rel).resolve()
    if root.resolve() not in p.parents:
        raise ValueError("path escapes corpus: " + rel)
    return p


def verify(root, execute=False):
    rows = index(root)
    if not rows:
        raise ValueError("empty corpus")
    seen = set()
    for row in rows:
        key = (row["version"], row["case"], row["profile"])
        if key in seen:
            raise ValueError("duplicate fixture: " + str(key))
        seen.add(key)
        if "abc" not in row:
            if row.get("bucket") not in ("es2abc-cant", "expected-negative-parse"):
                raise ValueError("artifact-less row outside compile-failure buckets: " + str(key))
            if digest(safe_path(root, row["source"])) != row["source_sha256"]:
                raise ValueError("hash mismatch: " + row["source"])
            continue
        abc = safe_path(root, row["abc"])
        if inspect(abc) != row["header"] or row["header"]["version"] != row["version"]:
            raise ValueError("header/hash mismatch: " + str(abc))
        for field in ["source", "pandasm"]:
            if digest(safe_path(root, row[field])) != row[field + "_sha256"]:
                raise ValueError("hash mismatch: " + row[field])
        metadata = read_json(abc.parent / "metadata.json")
        if metadata != row:
            raise ValueError("metadata/index mismatch: " + str(abc))
        if execute and row["runtime"]["status"] == "passed":
            actual = call("ark_js_vm", [abc], cwd=root)
            for field in ["exit_code", "stdout", "stderr", "timeout"]:
                if actual[field] != row["runtime"][field]:
                    raise ValueError("runtime mismatch: " + str(abc))
    return {"verified": len(rows), "execute": execute}


def export(dest, version=None, case=None, profile=None):
    corpus = ROOT / "corpus"
    rows = selected(index(corpus), version, case, profile)
    if not rows:
        raise ValueError("no fixtures match selection")
    dest = Path(dest).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise ValueError("export destination must be empty")
    dest.mkdir(parents=True, exist_ok=True)
    for row in rows:
        if "abc" not in row:
            continue  # compile-failure records carry no artifacts to export
        for rel in [row["source"], row["abc"], row["pandasm"],
                    str(Path(row["abc"]).parent / "metadata.json")]:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_path(corpus, rel), target)
    for name in ["build-info.json", "versions.json", "profiles.json"]:
        shutil.copyfile(corpus / name, dest / name)
    shutil.copytree(ROOT / "licenses", dest / "licenses")
    rows = [r for r in rows if "abc" in r]
    (dest / "index.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return verify(dest)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("info")
    for name in ["list", "export"]:
        s = sub.add_parser(name)
        for field in ["version", "case", "profile"]:
            s.add_argument("--" + field)
        if name == "export":
            s.add_argument("output")
    s = sub.add_parser("inspect"); s.add_argument("input")
    s = sub.add_parser("verify"); s.add_argument("--root", default=str(ROOT / "corpus")); s.add_argument("--execute", action="store_true")
    sub.add_parser("generate")
    s = sub.add_parser("compile")
    s.add_argument("input"); s.add_argument("output")
    s.add_argument("--version", required=True, choices=list(read_json(ROOT / "versions.json")))
    s.add_argument("--profile", default="baseline", choices=list(read_json(ROOT / "profiles.json")))
    s.add_argument("--mode", choices=["script", "module", "commonjs"], default="script")
    s = sub.add_parser("compile-matrix", help="compile and disassemble a new source across all six versions")
    s.add_argument("input"); s.add_argument("output")
    s.add_argument("--profile", default="baseline", choices=list(read_json(ROOT / "profiles.json")))
    s.add_argument("--mode", choices=["script", "module", "commonjs"], default="script")
    s = sub.add_parser("disassemble"); s.add_argument("input"); s.add_argument("output")
    s = sub.add_parser("run"); s.add_argument("input"); s.add_argument("--timeout", type=float, default=30)
    s = sub.add_parser("compare")
    s.add_argument("input", help="ABC produced/rewritten by abcd-rs")
    s.add_argument("--case", required=True); s.add_argument("--version", required=True)
    s.add_argument("--profile", default="baseline"); s.add_argument("--timeout", type=float, default=30)
    return p


def main():
    args = parser().parse_args()
    result = None
    exit_code = 0
    if args.command == "info":
        result = {"build": read_json(ROOT / "build-info.json"), "corpus": read_json(ROOT / "corpus/summary.json")}
    elif args.command == "list":
        rows = selected(index(ROOT / "corpus"), args.version, args.case, args.profile)
        for row in rows:
            print(json.dumps(row, sort_keys=True))
        return 0
    elif args.command == "generate":
        generate(); result = verify(ROOT / "corpus")
    elif args.command == "export":
        result = export(args.output, args.version, args.case, args.profile)
    elif args.command == "inspect":
        result = inspect(args.input)
    elif args.command == "verify":
        result = verify(Path(args.root).resolve(), args.execute)
    elif args.command == "compile":
        result = compile_one(Path(args.input).resolve(), args.output, args.version, args.profile, args.mode)
    elif args.command == "compile-matrix":
        source = Path(args.input).resolve()
        target = Path(args.output).resolve()
        if target.exists():
            raise ValueError("output already exists: " + str(target))
        target.mkdir(parents=True)
        shutil.copyfile(source, target / source.name)
        result = []
        for v in read_json(ROOT / "versions.json"):
            dest = target / v
            dest.mkdir()
            c = compile_one(source.name, dest / "input.abc", v, args.profile, args.mode, cwd=target)
            d = call("ark_disasm", [dest / "input.abc", dest / "reference.pa"])
            require_ok(d)
            row = {"version": v, "profile": args.profile, "compile": c,
                   "disassemble": d, "source_sha256": digest(source), "runtime": "not-run"}
            write_json(dest / "metadata.json", row)
            result.append(row)
    elif args.command == "disassemble":
        inspect(args.input)
        if Path(args.output).exists():
            raise ValueError("output already exists: " + args.output)
        result = call("ark_disasm", [Path(args.input).resolve(), Path(args.output).resolve()])
        require_ok(result)
    elif args.command == "run":
        inspect(args.input)
        result = call("ark_js_vm", [Path(args.input).resolve()], timeout=args.timeout)
        exit_code = 0 if result["exit_code"] == 0 else 1
    elif args.command == "compare":
        rows = selected(index(ROOT / "corpus"), args.version, args.case, args.profile)
        if len(rows) != 1 or rows[0]["runtime"]["status"] != "passed":
            raise ValueError("select exactly one fixture with a checked runtime oracle")
        row = rows[0]
        header = inspect(args.input)
        if header["version"] != row["version"]:
            raise ValueError("candidate ABC has wrong version")
        # No exact PA text comparison: entity offsets and debug records are meaningful.
        with tempfile.TemporaryDirectory(prefix="ark-compare-") as t:
            d = call("ark_disasm", [Path(args.input).resolve(), Path(t) / "candidate.pa"])
            require_ok(d)
        result = call("ark_js_vm", [Path(args.input).resolve()], timeout=args.timeout)
        result["matches"] = all(result[k] == row["runtime"][k] for k in ["exit_code", "stdout", "stderr", "timeout"])
        exit_code = 0 if result["matches"] else 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, KeyError) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
