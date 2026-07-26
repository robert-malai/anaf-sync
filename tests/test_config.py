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
    path = write_default_config(tmp_path / "config.toml", cifs=["12345678"])
    config = load_config(path)
    assert config.cifs == ["12345678"]
    assert config.direction is Direction.RECEIVED
    assert config.lookback_days == 60
    assert config.failure_retention_days == 90
    assert config.output.artifacts == [Artifact.ZIP, Artifact.PDF]
    assert "{issue_date:%Y}" in config.output.template


def test_written_cif_is_baked_in_and_normalised(tmp_path: Path) -> None:
    path = write_default_config(tmp_path / "config.toml", cifs=[" ro87654321 "])
    assert 'cif = "87654321"' in path.read_text(encoding="utf-8")
    assert load_config(path).cifs == ["87654321"]


def test_several_cifs_are_written_as_a_list(tmp_path: Path) -> None:
    path = write_default_config(tmp_path / "config.toml", cifs=["12345678", "87654321"])
    assert 'cifs = ["12345678", "87654321"]' in path.read_text(encoding="utf-8")
    assert load_config(path).cifs == ["12345678", "87654321"]


def test_a_bad_cif_is_rejected_before_anything_is_written(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="not numeric"):
        write_default_config(path, cifs=["not-a-cif"])
    with pytest.raises(ValueError, match="at least one CIF"):
        write_default_config(path, cifs=[])
    assert not path.exists()


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
    path = write_default_config(tmp_path / "config.toml", cifs=["12345678"])
    with pytest.raises(FileExistsError):
        write_default_config(path, cifs=["12345678"])
    write_default_config(path, cifs=["12345678"], force=True)  # explicit force is fine
