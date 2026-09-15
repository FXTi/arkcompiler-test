#!/usr/bin/env python3
"""Report source-generated pandasm mnemonic coverage by ABC version."""
import argparse
import collections
import json
from pathlib import Path
import re


def isa_mnemonics(path):
    result = set()
    for line in Path(path).read_text().splitlines():
        match = re.search(r"- sig:\s*([^\s]+)", line)
        if match:
            result.add(match.group(1))
    return result


def pandasm_mnemonics(path):
    result = collections.Counter()
    for file in Path(path).rglob("*.pa"):
        for line in file.read_text(errors="replace").splitlines():
            text = line.strip()
            if line.startswith("\t") and text and text[0].islower():
                result[text.split()[0]] += 1
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("isa")
    p.add_argument("corpus")
    args = p.parse_args()
    isa = isa_mnemonics(args.isa)
    corpus = Path(args.corpus)
    report = {}
    for version in sorted(x.name for x in corpus.iterdir() if x.is_dir() and re.match(r"^\d", x.name)):
        counts = pandasm_mnemonics(corpus / version)
        report[version] = {"present": sorted(counts), "missing": sorted(isa - set(counts)),
                           "present_count": len(counts), "missing_count": len(isa - set(counts))}
    print(json.dumps({"isa_count": len(isa), "versions": report}, indent=2))


if __name__ == "__main__":
    main()
