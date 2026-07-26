import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "recover_validation_checkout.sh"
EXPECTED = (
    "frontend/assets/dashboard_lg_remote.js",
    "install.sh",
    "tests/test_epic_09_lg_tv_frontend.py",
    "tests/test_hotfix_pack_21_runtime_config.py",
    "tests/test_smart_control_01a_lg.py",
)


def _repository(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    for relative in (*EXPECTED, "unrelated.txt"):
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"original:{relative}", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "recovery-test@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Recovery Test"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repository, check=True)
    return repository


def test_known_validation_changes_are_stashed_and_checkout_becomes_clean(tmp_path):
    repository = _repository(tmp_path)
    for relative in EXPECTED:
        (repository / relative).write_text(f"validation-copy:{relative}", encoding="utf-8")

    result = subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=repository, text=True,
    ) == ""
    stash_paths = subprocess.check_output(
        ["git", "stash", "show", "--name-only", "stash@{0}"],
        cwd=repository,
        text=True,
    ).splitlines()
    assert sorted(stash_paths) == sorted(EXPECTED)
    assert "ready for git pull --ff-only" in result.stdout


def test_unexpected_change_aborts_without_stashing_anything(tmp_path):
    repository = _repository(tmp_path)
    (repository / EXPECTED[0]).write_text("known validation change", encoding="utf-8")
    (repository / "unrelated.txt").write_text("user change", encoding="utf-8")
    status_before = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=repository, text=True,
    )

    result = subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "unexpected modified path: unrelated.txt" in result.stderr
    assert subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=repository, text=True,
    ) == status_before
    assert subprocess.run(
        ["git", "rev-parse", "--verify", "refs/stash"],
        cwd=repository,
        capture_output=True,
        check=False,
    ).returncode != 0
