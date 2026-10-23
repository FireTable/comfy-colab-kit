import asyncio
from pathlib import Path

from gate import session as sess


def test_reload_profiles_merges_from_disk(tmp_path, monkeypatch):
    calls = []

    def fake_load_all(refs, root):
        calls.append(tuple(refs))
        return {"new": "P-new", "a": "P-a2"}, {"bad": "boom"}

    monkeypatch.setattr("gate.profiles.load_all", fake_load_all)
    s = sess.Session.__new__(sess.Session)
    s.settings = type("S", (), {"profiles": ("x",)})()
    s.profiles = {"a": "P-a1", "keep": "P-k"}
    errs = s.reload_profiles()
    assert errs == {"bad": "boom"}
    assert s.profiles == {"a": "P-a2", "keep": "P-k", "new": "P-new"}
    assert calls == [("x",)]
