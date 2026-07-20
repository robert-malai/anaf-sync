"""Configuration loading."""

from pathlib import Path

import pytest

from anaf_sync.config import (
    Artifact,
    Direction,
    SyncConfig,
    load_config,
    write_default_config,
)


def test_default_config_file_is_valid(tmp_path: Path) -> None:
    path = write_default_config(tmp_path / "config.toml")
    config = load_config(path)
    assert config.cifs == ["12345678"]
    assert config.direction is Direction.RECEIVED
    assert config.lookback_days == 60
    assert config.failure_retention_days == 90
    assert config.output.artifacts == [Artifact.ZIP, Artifact.PDF]
    assert "{issue_date:%Y}" in config.output.template


def test_single_cif_and_ro_prefix_are_normalised() -> None:
    config = SyncConfig.model_validate({"cif": "RO12345678"})
    assert config.cifs == ["12345678"]


def test_non_numeric_cif_is_rejected() -> None:
    with pytest.raises(ValueError, match="not numeric"):
        SyncConfig.model_validate({"cif": "not-a-cif"})


def test_non_positive_failure_retention_is_rejected() -> None:
    with pytest.raises(ValueError, match="failure_retention_days"):
        SyncConfig.model_validate({"cif": "12345678", "failure_retention_days": 0})


def test_missing_config_has_a_helpful_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="anaf-sync init"):
        load_config(tmp_path / "absent.toml")


def test_init_refuses_to_overwrite(tmp_path: Path) -> None:
    path = write_default_config(tmp_path / "config.toml")
    with pytest.raises(FileExistsError):
        write_default_config(path)
    write_default_config(path, force=True)  # explicit force is fine
