import subprocess, tempfile

from core import launch


def _run(cmd):
    err = tempfile.TemporaryFile()
    proc = subprocess.Popen(["sh", "-c", cmd], stderr=err)
    got = []
    launch._surveiller_echec(proc, err, got.append)
    return got


def test_quick_failure_is_reported_with_stderr_tail():
    assert _run("echo 'error: app/x/y not installed' >&2; exit 1") == ["error: app/x/y not installed"]


def test_quick_failure_without_output_reports_exit_code():
    assert _run("exit 4") == ["exit code 4"]


def test_success_is_silent():
    assert _run("exit 0") == []


def test_long_running_game_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(launch, "EXIT_GRACE_SEC", 0.2)
    assert _run("sleep 1; exit 1") == []


def test_launcher_url_only_for_web_sources():
    assert launch.launcher_url("flatpak") == "https://flathub.org"
    assert launch.launcher_url("steam") is None and launch.launcher_url("lutris") is None
