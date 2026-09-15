"""Shared test setup.

Every test that starts the app would otherwise read and create the real
~/.config/storagemark/rules.toml. Point the config directory at a
per-test temporary path so a test run never touches the user's own file.
Tests that care about the file override the variable themselves.
"""
import pytest


@pytest.fixture(autouse=True)
def isolate_storagemark_config(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGEMARK_CONFIG_DIR", str(tmp_path / "sm-config"))
