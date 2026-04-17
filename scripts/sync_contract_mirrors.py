"""Copy or verify repo-root JSON mirrors of bundled spell/spellbook schema and default policy.

Canonical sources live under axiomurgy/bundled/. Repository-root copies exist for stable URLs and docs.

  python scripts/sync_contract_mirrors.py          # overwrite mirrors from bundled
  python scripts/sync_contract_mirrors.py --check  # exit 1 if any mirror differs (CI)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve().parent
    root = here.parent
    bundled = root / "axiomurgy" / "bundled"
    if not bundled.is_dir():
        raise SystemExit(f"Expected bundled directory at {bundled}")
    return root


def _pairs(root: Path) -> list[tuple[Path, Path]]:
    b = root / "axiomurgy" / "bundled"
    return [
        (b / "spell.schema.json", root / "spell.schema.json"),
        (b / "spellbook.schema.json", root / "spellbook.schema.json"),
        (b / "policies" / "default.policy.json", root / "policies" / "default.policy.json"),
    ]


def _bytes_equal(a: Path, b: Path) -> bool:
    return a.read_bytes() == b.read_bytes()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare bundled files to repo mirrors; exit 1 on mismatch.",
    )
    args = parser.parse_args(argv)
    root = _repo_root()
    pairs = _pairs(root)
    errors: list[str] = []

    for canonical, mirror in pairs:
        if not canonical.is_file():
            errors.append(f"missing canonical file: {canonical}")
            continue
        if args.check:
            if not mirror.is_file():
                errors.append(f"missing mirror (expected copy of {canonical.name}): {mirror}")
                continue
            if not _bytes_equal(canonical, mirror):
                errors.append(f"drift: {mirror} differs from {canonical}")
        else:
            mirror.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(canonical, mirror)

    if errors:
        print("Contract mirror check failed:", file=sys.stderr)
        for line in errors:
            print(f"  {line}", file=sys.stderr)
        return 1

    if args.check:
        print("OK: repo-root mirrors match axiomurgy/bundled/ (byte-identical).")
    else:
        print("OK: copied bundled contracts to repo-root mirrors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
