#!/usr/bin/env python3
"""Stage an existing x64.release build; do not rebuild or copy the monorepo."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]

TEST262_COMMIT = "747bed2e8aaafe8fdf2c65e8a10dd7ae64f66c47"
TEST262_REPO = "https://github.com/tc39/test262.git"
MANIFEST_TAG_DEFAULT = "OpenHarmony-7.0-Release"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, obj):
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


TEST262_TREE = "108f92392a2eb006170782c598a3336617715b87"  # git tree of TEST262_COMMIT


def verify_test262():
    """Verify the test262 git submodule is checked out exactly at the pinned
    commit; performs no download. Integrity is git object identity itself:
    HEAD must equal TEST262_COMMIT (whose tree is TEST262_TREE) and the
    worktree must be clean. On dabai, github access goes through the proxy:
    proxychains4 -q git submodule update --init"""
    checkout = REPO / "test262"
    hint = ("run: git submodule update --init (on dabai, github access goes "
            "through the proxy: proxychains4 -q git submodule update --init)")
    if not (checkout / ".git").exists():
        raise ValueError("test262 submodule is not initialized; " + hint)
    head = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    if head != TEST262_COMMIT:
        raise ValueError(
            "test262 submodule is at %s, expected %s; " % (head, TEST262_COMMIT) + hint)
    tree = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD^{tree}"], text=True).strip()
    if tree != TEST262_TREE:
        raise ValueError("test262 tree hash mismatch: %s != %s" % (tree, TEST262_TREE))
    if subprocess.call(["git", "-C", str(checkout), "diff", "--quiet", "HEAD", "--"]):
        raise ValueError("test262 submodule worktree is dirty; do not edit submodule contents")
    return checkout, tree


def parse_frontmatter(text, path):
    """Parse the simple YAML subset used by test262 /*--- ... ---*/ blocks."""
    start = text.find("/*---")
    if start < 0:
        return {}
    end = text.find("---*/", start)
    if end < 0:
        raise ValueError("unterminated frontmatter: " + path)
    lines = text[start + 5:end].splitlines()
    indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    if indents and min(indents):
        pad = min(indents)
        lines = [l[pad:] if l.strip() else l for l in lines]
    meta = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line[0] in " \t":
            raise ValueError("unexpected indentation in frontmatter of %s: %r" % (path, line))
        key, sep, value = line.strip().partition(":")
        if not sep:
            raise ValueError("frontmatter line without key in %s: %r" % (path, line))
        key = key.strip()
        value = value.strip()
        if value.startswith("["):
            while "]" not in value:
                if i >= len(lines):
                    raise ValueError("unterminated inline list in frontmatter of " + path)
                value += " " + lines[i].strip()
                i += 1
            inner = value[1:value.index("]")]
            meta[key] = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
        elif value in (">", "|", ">-", "|-", ">+", "|+"):
            folded = []
            while i < len(lines) and (not lines[i].strip() or lines[i][0] in " \t"):
                folded.append(lines[i].strip())
                i += 1
            meta[key] = " ".join(folded).strip()
        elif value == "":
            items, mapping = [], {}
            while i < len(lines) and lines[i].strip() and lines[i][0] in " \t":
                sub = lines[i].strip()
                i += 1
                if sub.startswith("- "):
                    items.append(sub[2:].strip().strip("'\""))
                else:
                    k2, sep2, v2 = sub.partition(":")
                    if not sep2:
                        raise ValueError("bad nested frontmatter in %s: %r" % (path, sub))
                    mapping[k2.strip()] = v2.strip().strip("'\"")
            meta[key] = items if items else mapping
        else:
            meta[key] = value.strip("'\"")
    return meta


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def read_ci_names(ci_list):
    """CI_tests.txt, order-preserving and deduplicated (the upstream list
    itself repeats a handful of entries)."""
    seen, names = set(), []
    for line in ci_list.read_text().splitlines():
        name = line.strip()
        if name and not name.startswith("#") and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def stage_test262(checkout, tree, frontend, sources, cases):
    """Stage the arkcompiler CI_tests.txt subset of test262, preprocessed per
    INTERPRETING.md: sta.js + assert.js inlined, single strict-mode pass.
    Tests needing more (extra includes, async, module, raw/noStrict) become
    counted skip entries, never silent drops."""
    ci_list = frontend / "test262/CI_tests.txt"
    if not ci_list.exists():
        raise ValueError("missing test262 CI list: " + str(ci_list))
    names = read_ci_names(ci_list)
    harness = '"use strict";\n' + (checkout / "harness/sta.js").read_text() + "\n" \
        + (checkout / "harness/assert.js").read_text() + "\n"
    for name in names:
        source = checkout / "test" / name
        cid = "test262/" + (name[:-3] if name.endswith(".js") else name)
        if not source.is_file():
            # arkcompiler's CI list tracks its own fork pin; entries absent at
            # the upstream pin are a counted bucket, never a silent drop.
            cases.append({"id": cid, "tags": ["test262", name.split("/", 1)[0]],
                          "origin": {"kind": "test262", "license": "BSD-3-Clause",
                                     "upstream-commit": TEST262_COMMIT, "path": "test/" + name},
                          "test262": {"flags": [], "negative": None},
                          "skip": "missing"})
            continue
        text = source.read_text(errors="replace")
        meta = parse_frontmatter(text, name)
        flags = as_list(meta.get("flags"))
        includes = as_list(meta.get("includes"))
        negative = meta.get("negative") or None
        if negative is not None and not isinstance(negative, dict):
            raise ValueError("unexpected negative block in " + name)
        if "raw" in flags or "noStrict" in flags:
            skip = "flags"
        elif "module" in flags:
            skip = "module"
        elif "async" in flags:
            skip = "async"
        elif includes:
            skip = "includes"
        else:
            skip = None
        case = {"id": cid, "tags": ["test262", name.split("/", 1)[0]],
                "origin": {"kind": "test262", "license": "BSD-3-Clause",
                           "upstream-commit": TEST262_COMMIT, "path": "test/" + name},
                "test262": {"flags": flags, "negative": negative}}
        if skip:
            case["skip"] = skip
        else:
            case["source"] = "test262/" + name
            case["versions"] = ["24.0.0.0"]
            case["profiles"] = ["baseline"]
            case["runtime_reason"] = "test262 raw behavior record"
            dest = sources / "test262" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(harness + text)
        cases.append(case)
    return {"commit": TEST262_COMMIT, "tree": tree,
            "ci_list": "arkcompiler/ets_frontend/test262/CI_tests.txt",
            "ci_list_sha256": sha(ci_list), "tests": len(names)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--openharmony", type=Path, default=REPO / "OpenHarmony-7.0-Release")
    p.add_argument("--update-lock", action="store_true", help="explicitly accept new upstream revisions and selected source hashes")
    args = p.parse_args()
    oh = args.openharmony.resolve()
    out = oh / "out/x64.release"
    frontend = oh / "arkcompiler/ets_frontend"
    test = frontend / "es2panda/test"
    cases = json.loads((REPO / "config/local-cases.json").read_text())
    revisions = {}
    for name in ["ets_frontend", "runtime_core", "ets_runtime"]:
        component = oh / "arkcompiler" / name
        revisions[name] = subprocess.check_output(["git", "-C", str(component), "rev-parse", "HEAD"], text=True).strip()
        if subprocess.check_output(["git", "-C", str(component), "diff", "HEAD", "--"], text=True):
            raise ValueError("upstream tracked source is dirty: " + name)
    files = {}
    source_locations = {}
    overrides = {e["path"]: e for e in json.loads((REPO / "config/upstream-cases.json").read_text())}
    # These are the direct source inputs in the three agreed compiler suites.
    # Expected-output files, harnesses and C++ tests are deliberately excluded.
    candidate_paths = []
    for suite in ["bytecode", "optimizer", "type_extractor"]:
        candidate_paths += sorted((test / suite).rglob("*.js"))
        candidate_paths += sorted((test / suite).rglob("*.ts"))
    candidate_paths = [p for p in candidate_paths if "-expected" not in p.stem]
    version_control = test / "version_control"
    version_specs = {}
    vc_rules = [
        ("API11", "11.0.2.0", ["--target-api-version=11"], ["syntax_feature", "bytecode_feature"]),
        ("API12beta1_and_beta2", "12.0.2.0", ["--target-api-version=12", "--target-api-sub-version=beta1"], ["syntax_feature", "bytecode_feature"]),
        ("API12beta3", "12.0.6.0", ["--target-api-version=12", "--target-api-sub-version=beta3"], ["syntax_feature", "bytecode_feature"]),
        ("API18", "13.0.1.0", ["--target-api-version=18"], ["bytecode_feature"]),
        ("API20", "13.0.1.0", ["--target-api-version=20", "--enable-annotations"], ["bytecode_feature"]),
        ("API24", "24.0.0.0", ["--target-api-version=24", "--enable-callable-name"], ["bytecode_feature"]),
    ]
    for directory, version, compile_args, suites in vc_rules:
        for suite in suites:
            root = version_control / directory / suite
            for source in sorted(root.rglob("*.js")) + sorted(root.rglob("*.ts")):
                if "-expected" in source.stem:
                    continue
                candidate_paths.append(source)
                version_specs[str(source)] = (version, compile_args, "module" if source.suffix == ".ts" or suite == "syntax_feature" else "script")
    runtime_test = oh / "arkcompiler/ets_runtime/test/executiontest/js"
    candidate_paths += sorted(runtime_test.glob("*.js"))
    for source in candidate_paths:
        vc = version_specs.get(str(source))
        try:
            relative = str(source.relative_to(test))
            component, suite_path = "ets_frontend", "es2panda/test/" + relative
        except ValueError:
            relative = "ets_runtime/test/executiontest/js/" + source.name
            component, suite_path = "ets_runtime", relative
        entry = overrides.get(relative, {})
        files[relative] = sha(source)
        source_locations[relative] = source
        tags = entry.get("tags", [relative.split("/", 1)[0], "upstream"])
        mode = entry.get("mode", "module" if relative.endswith((".js", ".ts")) and
                              any(line.lstrip().startswith(("import ", "export "))
                                  for line in source.read_text(errors="replace").splitlines()) else "script")
        if "commonjs" in relative:
            mode = "commonjs"
        case = {"id": "upstream/" + str(Path(relative).with_suffix("")),
                "source": "upstream/" + relative, "tags": tags,
                "origin": {"kind": "upstream", "component": component,
                           "revision": revisions[component], "path": suite_path,
                           "license": "Apache-2.0"},
                "mode": mode}
        if vc:
            case["versions"] = [vc[0]]
            case["compile_args"] = vc[1]
            case["mode"] = vc[2]
            case["tags"] = ["version-control", "isa", "source"]
        for key in ["mode", "expected_stdout", "runtime_reason", "versions", "profiles"]:
            if key in entry:
                case[key] = entry[key]
        if "expected_file" in entry:
            expected = test / entry["expected_file"]
            files[entry["expected_file"]] = sha(expected)
            case["expected_stdout"] = expected.read_text()
            case["origin"]["expected_path"] = "es2panda/test/" + entry["expected_file"]
        cases.append(case)
    checkout, test262_tree = verify_test262()
    ci_list = frontend / "test262/CI_tests.txt"
    if not ci_list.exists():
        raise ValueError("missing test262 CI list: " + str(ci_list))
    ci_names = read_ci_names(ci_list)
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case IDs")
    lock_path = REPO / "upstream.lock.json"
    previous = json.loads(lock_path.read_text()) if lock_path.exists() else {}
    lock = {"manifest_tag": previous.get("manifest_tag", MANIFEST_TAG_DEFAULT),
            "revisions": revisions, "selected_files_sha256": files,
            "isa_sha256": sha(oh / "arkcompiler/runtime_core/isa/isa.yaml"),
            "test262": {"commit": TEST262_COMMIT, "tree": test262_tree,
                        "ci_list": "arkcompiler/ets_frontend/test262/CI_tests.txt",
                        "ci_list_sha256": sha(ci_list), "tests": len(ci_names)}}
    if args.update_lock:
        write(lock_path, lock)
    elif not lock_path.exists() or json.loads(lock_path.read_text()) != lock:
        raise ValueError("upstream lock mismatch/missing; inspect changes then use --update-lock")
    stage = REPO / ".stage"
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "bin").mkdir(parents=True)
    (stage / "lib").mkdir()
    binaries = {
        "bin/es2abc": "arkcompiler/ets_frontend/es2abc",
        "bin/ark_disasm": "arkcompiler/runtime_core/ark_disasm",
        "bin/ark_js_vm": "arkcompiler/ets_runtime/ark_js_vm",
        "lib/libark_jsruntime.so": "arkcompiler/ets_runtime/libark_jsruntime.so"}
    artifact_info = {}
    for target, source in binaries.items():
        src = out / source
        if src.read_bytes()[:5] != b"\x7fELF\x02":
            raise ValueError("expected ELF64 artifact: " + str(src))
        shutil.copy2(src, stage / target)
        artifact_info[target] = {"sha256": sha(src), "bytes": src.stat().st_size,
                                 "ldd": subprocess.check_output(["ldd", str(src)], text=True)
                                 .replace(str(oh) + "/", "<build>/")}
    sources = stage / "sources"
    shutil.copytree(REPO / "cases", sources / "local")
    for rel, source in source_locations.items():
        dst = sources / "upstream" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dst)
    stage_test262(checkout, test262_tree, frontend, sources, cases)
    # The image is consumed as a non-root user; never propagate host umasks.
    for path in sorted(sources.rglob("*")):
        path.chmod(0o755 if path.is_dir() else 0o644)
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case IDs")
    pandasm = stage / "pandasm"
    pandasm.mkdir()
    pandasm_index = []
    for root in [oh / "arkcompiler/runtime_core/tests/checked",
                 oh / "arkcompiler/runtime_core/tests/regression"]:
        for source in sorted(root.rglob("*.pa")):
            rel = str(source.relative_to(oh / "arkcompiler"))
            dst = pandasm / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dst)
            pandasm_index.append({"path": rel, "sha256": sha(source),
                                  "origin": "runtime_core", "license": "Apache-2.0"})
    write(pandasm / "index.json", {"schema_version": 1, "files": pandasm_index})
    for name in ["versions.json", "profiles.json"]:
        shutil.copy2(REPO / "config" / name, stage / name)
    # Keep notices with copied source and statically linked third-party code.
    licenses = stage / "licenses"
    licenses.mkdir()
    shutil.copy2(REPO / "LICENSE", licenses / "arkcompiler-test-LICENSE")
    shutil.copy2(checkout / "LICENSE", licenses / "test262-LICENSE")
    for component in [oh / "arkcompiler", oh / "third_party"]:
        for f in sorted(component.rglob("*")):
            if f.is_file() and f.name.upper().startswith(("LICENSE", "LICENCE", "NOTICE", "COPYING", "COPYRIGHT")):
                dst = licenses / f.relative_to(oh)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
    write(stage / "cases.json", cases)
    generator_hashes = {}
    for directory in ["app", "scripts", "config", "cases"]:
        for path in sorted((REPO / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                generator_hashes[str(path.relative_to(REPO))] = sha(path)
    write(stage / "build-info.json", {"schema_version": 1, "upstream": lock,
          "build": "x64.release", "platform": "linux/amd64", "artifacts": artifact_info,
          "build_command": "python3 ark.py x64.release es2panda ark_disasm ark_js_vm -j8",
          "generator_revision": subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip() if (REPO / ".git/refs/heads/main").exists() else "initial-working-tree",
          "cases_sha256": sha(stage / "cases.json")})
    info = json.loads((stage / "build-info.json").read_text())
    info["generator_files_sha256"] = generator_hashes
    write(stage / "build-info.json", info)
    print(json.dumps({"stage": str(stage), "cases": len(cases), "artifacts": list(binaries)}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as e:
        sys.exit(str(e))
