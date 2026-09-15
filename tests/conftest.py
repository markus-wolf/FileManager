"""Shared test setup.

Every test that starts the app would otherwise read and create the real
~/.config/storagemark/rules.toml. Point the config directory at a
per-test temporary path so a test run never touches the user's own file.
Tests that care about the file override the variable themselves.

XDG_DATA_HOME is redirected for the same reason: on Linux the Trash lives
under it, and a removal test must not fill the user's own Trash.
"""
import pytest


@pytest.fixture(autouse=True)
def isolate_storagemark_config(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGEMARK_CONFIG_DIR", str(tmp_path / "sm-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "sm-data"))
