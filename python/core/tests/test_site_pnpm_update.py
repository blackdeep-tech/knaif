"""Exercise updater dispatch and failure recovery without changing the toolchain."""

import subprocess

import pytest

from scripts import site_pnpm_update as updater


@pytest.fixture
def commands(monkeypatch):
    calls = []
    monkeypatch.setattr(updater.shutil, "which", lambda name: name)

    def run(args, *, cwd, check):
        assert cwd == updater.SITE
        assert check
        calls.append(args)

    monkeypatch.setattr(updater.subprocess, "run", run)
    return calls


def test_standalone_updates_site_without_touching_global_install(monkeypatch, commands):
    monkeypatch.setattr(updater, "is_corepack_shim", lambda path: False)
    updater.update("12.4.1")
    assert commands == [("pnpm", "self-update", "12.4.1"), ("pnpm", "install")]


def test_corepack_migrates_before_launching_pnpm(monkeypatch, commands):
    monkeypatch.setattr(updater, "is_corepack_shim", lambda path: True)
    updater.update("latest")
    assert commands == [
        ("corepack", "disable", "pnpm"),
        ("npm", "install", "--global", "pnpm@latest"),
        ("pnpm", "self-update", "latest"),
        ("pnpm", "install"),
    ]


def test_failed_migration_restores_launcher_and_does_not_install_site(monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda name: name)
    monkeypatch.setattr(updater, "is_corepack_shim", lambda path: True)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == "npm":
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(updater.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        updater.update("latest")
    assert calls == [
        ("corepack", "disable", "pnpm"),
        ("npm", "install", "--global", "pnpm@latest"),
        ("corepack", "enable", "pnpm"),
    ]


def test_missing_pnpm_bootstraps_with_npm(monkeypatch, commands):
    def which(name):
        return None if name == "pnpm" and not commands else name

    monkeypatch.setattr(updater.shutil, "which", which)
    updater.update("12")
    assert commands == [
        ("npm", "install", "--global", "pnpm@12"),
        ("pnpm", "self-update", "12"),
        ("pnpm", "install"),
    ]


@pytest.mark.parametrize("version", ["latest & echo bad", "--global", '12"', "$(echo bad)"])
def test_invalid_version_never_launches_a_command(version, commands):
    with pytest.raises(ValueError):
        updater.update(version)
    assert not commands


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b'node "%dp0%/node_modules/corepack/dist/pnpm.js" %*', True),
        (b'node "$basedir/node_modules/pnpm/bin/pnpm.cjs" "$@"', False),
        (b"MZ\x00\x01\xff", False),
    ],
)
def test_launcher_detection(tmp_path, content, expected):
    launcher = tmp_path / "pnpm.cmd"
    launcher.write_bytes(content)
    assert updater.is_corepack_shim(str(launcher)) is expected
