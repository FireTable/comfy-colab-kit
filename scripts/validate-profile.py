#!/usr/bin/env python3
"""Validate profiles against schemas/profile.schema.json and gate/profiles.py rules.
Usage: scripts/validate-profile.py [examples/profiles/*]"""
import json
import sys
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gate.profiles import ProfileError, load_profile  # noqa: E402

schema = json.loads((ROOT / "schemas/profile.schema.json").read_text())
targets = [Path(a) for a in sys.argv[1:]] or sorted((ROOT / "examples/profiles").iterdir())
bad = 0
for t in targets:
    f = t / "profile.yaml" if t.is_dir() else t
    try:
        jsonschema.validate(yaml.safe_load(f.read_text()), schema)
        p = load_profile(f)
        print(f"ok  {p.id:<16} {len(p.workflows)} workflows, default download ~{p.total_size_gb()} GB")
    except (jsonschema.ValidationError, ProfileError, OSError) as e:
        bad = 1
        print(f"ERR {f}: {getattr(e, 'message', e)}")
sys.exit(bad)
