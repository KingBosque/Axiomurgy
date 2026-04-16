#!/usr/bin/env python3
"""Compatibility entry point for the axiomurgy package."""

import sys

from axiomurgy import *
from axiomurgy.cli import main
from axiomurgy.cli import _revolution_dir_from_run_manifest
from axiomurgy.ouroboros import _admissibility_status_rank


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
