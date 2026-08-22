import json

from backend import frontend_asset_version


def _write_release(tmp_path, marker):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (tmp_path / "VERSION").write_text("1.0.18\n", encoding="utf-8")
    (tmp_path / ".smart-condo-release.json").write_text(
        json.dumps(marker), encoding="utf-8"
    )
    return frontend


def test_valid_release_marker_wins_over_stale_runtime_git(tmp_path, monkeypatch):
    commit = "a" * 40
    frontend = _write_release(
        tmp_path,
        {
            "version": "1.0.18",
            "commit": commit,
            "source": "git-archive",
            "generated_at": "2026-08-22T12:00:00+07:00",
        },
    )
    monkeypatch.setattr(frontend_asset_version, "FRONTEND_DIR", frontend)
    monkeypatch.setattr(frontend_asset_version, "_git_revision", lambda: "stale-git")

    assert frontend_asset_version.build_version() == commit[:12]


def test_invalid_release_marker_falls_back_without_blocking_startup(tmp_path, monkeypatch):
    frontend = _write_release(
        tmp_path,
        {
            "version": "1.0.17",
            "commit": "b" * 40,
            "source": "git-archive",
            "generated_at": "2026-08-22T12:00:00+07:00",
        },
    )
    monkeypatch.setattr(frontend_asset_version, "FRONTEND_DIR", frontend)
    monkeypatch.setattr(frontend_asset_version, "_git_revision", lambda: "git-fallback")

    assert frontend_asset_version.build_version() == "git-fallback"


def test_explicit_build_version_remains_highest_priority(tmp_path, monkeypatch):
    frontend = _write_release(
        tmp_path,
        {
            "version": "1.0.18",
            "commit": "c" * 40,
            "source": "git-archive",
            "generated_at": "2026-08-22T12:00:00+07:00",
        },
    )
    monkeypatch.setattr(frontend_asset_version, "FRONTEND_DIR", frontend)
    monkeypatch.setenv("DASHBOARD_BUILD_VERSION", "manual build/42")

    assert frontend_asset_version.build_version() == "manualbuild42"
