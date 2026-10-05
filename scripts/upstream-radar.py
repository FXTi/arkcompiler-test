#!/usr/bin/env python3
"""Upstream drift radar for the OpenHarmony manifest repo (GitHub mirror
FXTi/ark_standalone_build; gitee times out from GH runners). Radar-only: never builds, never gates,
never auto-merges.

Picks the newest OpenHarmony-* ref (branches and tags) by tip COMMITTER
DATE -- deliberately no version parsing. On drift vs upstream.lock.json's
manifest_tag it creates or updates ONE standing PR per ref with the human
build/publish checklist; a closed PR means wont-port and is never
reopened. Any infrastructure failure exits nonzero and the workflow opens
ONE standing issue instead.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import traceback
import urllib.request
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[1]
LOCK = REPO / "upstream.lock.json"
MANIFEST_REPO = os.environ.get("MANIFEST_REPO", "https://github.com/FXTi/ark_standalone_build")
RAW = "https://raw.githubusercontent.com/FXTi/ark_standalone_build"
COMPONENTS = {"arkcompiler/ets_frontend": "ets_frontend",
              "arkcompiler/runtime_core": "runtime_core",
              "arkcompiler/ets_runtime": "ets_runtime"}


def run(args, cwd=None):
    return subprocess.check_output(args, text=True, cwd=cwd).strip()


def manifest_refs():
    out = run(["git", "ls-remote", MANIFEST_REPO,
               "refs/heads/OpenHarmony-*", "refs/tags/OpenHarmony-*"])
    refs = []
    for line in out.splitlines():
        sha, ref = line.split("\t")
        if ref.endswith("^{}"):
            continue
        refs.append((ref, sha))
    if not refs:
        raise RuntimeError("no OpenHarmony-* refs found at " + MANIFEST_REPO)
    return refs


def newest_by_commit_date(refs):
    with tempfile.TemporaryDirectory(prefix="radar-") as t:
        run(["git", "-C", t, "init", "-q"])
        best = None
        for ref, sha in refs:
            run(["git", "-C", t, "fetch", "-q", "--depth", "1", MANIFEST_REPO, sha])
            stamp = int(run(["git", "-C", t, "show", "-s", "--format=%ct", "FETCH_HEAD"]))
            if best is None or stamp > best[0]:
                best = (stamp, ref, sha)
        return best


def fetch_xml(ref_name, path):
    url = "%s/%s/%s" % (RAW, ref_name, path)
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def collect_manifest(ref_name):
    """Parse default.xml at the ref, following <include> elements."""
    projects, remotes = {}, {}
    default_revision = None
    seen, stack = set(), ["default.xml"]
    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        root = ET.fromstring(fetch_xml(ref_name, path))
        for include in root.findall("include"):
            stack.append(include.get("name"))
        for remote in root.findall("remote"):
            remotes[remote.get("name")] = remote.get("fetch")
        default = root.find("default")
        if default is not None and default.get("revision"):
            default_revision = default.get("revision")
        for project in root.findall("project"):
            key = project.get("path") or project.get("name")
            projects[key] = {"name": project.get("name"),
                             "revision": project.get("revision"),
                             "remote": project.get("remote")}
    return projects, remotes, default_revision


def resolve_sha(project, remotes, default_revision):
    revision = project["revision"] or default_revision
    if not revision:
        raise RuntimeError("project %s has no revision" % project["name"])
    if re.fullmatch(r"[0-9a-f]{40}", revision):
        return revision
    fetch = remotes.get(project["remote"])
    if not fetch:
        raise RuntimeError("unknown remote %r for %s" % (project["remote"], project["name"]))
    url = fetch.rstrip("/") + "/" + project["name"]
    out = run(["git", "ls-remote", url, revision])
    for line in out.splitlines():
        sha, ref = line.split("\t")
        if ref in ("refs/heads/" + revision, "refs/tags/" + revision, revision):
            return sha
    raise RuntimeError("cannot resolve %r at %s" % (revision, url))


def component_revisions(ref_name):
    projects, remotes, default_revision = collect_manifest(ref_name)
    revisions = {}
    for path, key in COMPONENTS.items():
        if path not in projects:
            raise RuntimeError("manifest at %s lacks project path %s" % (ref_name, path))
        revisions[key] = resolve_sha(projects[path], remotes, default_revision)
    return revisions


def render_pr_body(tag, tip, stamp, old_tag, revisions):
    rows = "\n".join("| `%s` | `%s` |" % (k, v) for k, v in sorted(revisions.items()))
    return """## Upstream manifest drift: `{tag}`

The weekly radar picked `{tag}` as the newest `OpenHarmony-*` ref of
`ark_standalone_build/manifest` by tip committer date (tip `{tip}`). This
repository currently tracks `{old}`.

This PR updates `upstream.lock.json`: `manifest_tag` and the component
`revisions` resolved from the manifest XML at that ref. **No build, no
sync was performed.** Merging is a human decision; closing this PR means
wont-port and the radar will not re-propose `{tag}`.

| component | revision at `{tag}` |
|---|---|
{rows}

### Human build/publish checklist (on dabai)

1. Sync the monorepo to `{tag}` (`repo init -b {tag}` / `repo sync` in `~/ark/OpenHarmony-*`).
2. Rebuild: `python3 ark.py x64.release es2panda ark_disasm ark_js_vm -j8`.
3. `python3 scripts/prepare.py --update-lock`; review and commit the lock changes.
4. `make build && make test`.
5. Merge this PR so main carries the new lock.
6. `make push` to publish the image.
7. Bump the pinned image digest (`@sha256:...`, never `:latest`) in abcd-rs.

_Generated by `.github/workflows/upstream-radar.yml`; radar run at Unix {stamp}._
""".format(tag=tag, tip=tip, old=old_tag, rows=rows, stamp=stamp)


def git(*args):
    return run(["git"] + list(args), cwd=REPO)


def main():
    lock = json.loads(LOCK.read_text())
    old_tag = lock.get("manifest_tag")
    stamp, ref, tip = newest_by_commit_date(manifest_refs())
    tag = ref.split("/", 2)[2]
    print("newest OpenHarmony-* ref: %s tip %s committer %d (tracked: %s)"
          % (tag, tip, stamp, old_tag))
    if tag == old_tag:
        print("no drift; nothing to do")
        return 0
    branch = "radar/manifest-" + tag
    prs = json.loads(run(["gh", "pr", "list", "--state", "all", "--head", branch,
                          "--json", "number,state,url"]))
    if any(p["state"] != "OPEN" for p in prs):
        print("a closed/merged PR already exists for %s; wont-port, skipping" % branch)
        return 0
    revisions = component_revisions(tag)
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    if prs:
        git("fetch", "origin", branch)
        git("checkout", branch)
        git("reset", "--hard", "origin/" + branch)
    else:
        git("fetch", "origin", "main")
        git("checkout", "-b", branch, "origin/main")
    updated = json.loads(LOCK.read_text())
    updated["manifest_tag"] = tag
    updated["revisions"] = revisions
    LOCK.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n")
    if not git("status", "--porcelain"):
        print("lock already up to date on " + branch)
        return 0
    git("add", "upstream.lock.json")
    git("commit", "-m", "radar: track manifest %s\n\nmanifest_tag -> %s; component revisions resolved\nfrom the manifest XML at that ref. No build, no sync." % (tag, tag))
    git("push", "-u", "origin", branch)
    body = render_pr_body(tag, tip, stamp, old_tag, revisions)
    if prs:
        run(["gh", "pr", "edit", str(prs[0]["number"]), "--body", body])
        print("updated standing PR " + prs[0]["url"])
    else:
        out = run(["gh", "pr", "create", "--title",
                   "radar: upstream manifest drift -> %s" % tag,
                   "--body", body, "--head", branch, "--base", "main"])
        print("opened PR " + out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
