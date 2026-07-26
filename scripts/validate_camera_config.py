#!/usr/bin/env python3
"""Validate camera inventory without printing configuration or secret values."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.camera_inventory_schema import CameraConfigError, validate_camera_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--placeholder-mode", action="store_true")
    args = parser.parse_args()
    try:
        payload = json.loads(args.path.read_text(encoding="utf-8"))
        validated = validate_camera_config(payload, placeholder_mode=args.placeholder_mode)
    except (OSError, json.JSONDecodeError, CameraConfigError) as exc:
        print(f"camera config invalid: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"camera config valid: {len(validated['cameras'])} camera entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
