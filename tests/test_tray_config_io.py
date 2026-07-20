"""tomlkit round-trip: comment preservation, minimal diffs, validation (no Qt)."""

from pathlib import Path

import pytest
import tomlkit
from pydantic import ValidationError

from anaf_sync.config import load_config, write_default_config
from anaf_sync.tray import config_io
from anaf_sync.tray.config_io import SettingsForm


def _form_from(path: Path, **overrides: object) -> SettingsForm:
    cfg = load_config(path)
    base = {
        "cifs": cfg.cifs,
        "direction": cfg.direction.value,
        "lookback_days": cfg.lookback_days,
        "directory": str(cfg.output.directory),
        "template": cfg.output.template,
        "artifacts": [a.value for a in cfg.output.artifacts],
    }
    base.update(overrides)
    return SettingsForm(**base)  # type: ignore[arg-type]


def test_changing_one_key_leaves_the_rest_byte_for_byte(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_default_config(path)
    original = path.read_text(encoding="utf-8")

    doc = config_io.load(path)
    config_io.apply(doc, _form_from(path, direction="both"))
    config_io.save(doc, path)

    updated = path.read_text(encoding="utf-8")
    # Only the direction value changed; every comment and blank line survives.
    assert updated == original.replace('direction = "received"', 'direction = "both"')


def test_comments_survive_and_config_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_default_config(path)

    doc = config_io.load(path)
    config_io.apply(doc, _form_from(path, lookback_days=30))
    config_io.save(doc, path)

    text = path.read_text(encoding="utf-8")
    assert "# anaf-sync configuration." in text
    assert "lookback_days = 30" in text
    assert load_config(path).lookback_days == 30


def test_single_cif_switches_to_list_when_multiple(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_default_config(path)

    doc = config_io.load(path)
    config_io.apply(doc, _form_from(path, cifs=["12345678", "87654321"]))
    config_io.save(doc, path)

    reloaded = load_config(path)
    assert reloaded.cifs == ["12345678", "87654321"]
    text = path.read_text(encoding="utf-8")
    assert "cifs" in text


def test_artifacts_and_template_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_default_config(path)

    doc = config_io.load(path)
    config_io.apply(
        doc,
        _form_from(
            path, artifacts=["zip", "xml", "metadata"], template="{cif}/{number}"
        ),
    )
    config_io.save(doc, path)

    reloaded = load_config(path)
    assert [a.value for a in reloaded.output.artifacts] == ["zip", "xml", "metadata"]
    assert reloaded.output.template == "{cif}/{number}"


def test_validate_rejects_empty_cifs_without_touching_the_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    write_default_config(path)
    before = path.read_text(encoding="utf-8")

    doc = config_io.load(path)
    config_io.apply(doc, _form_from(path, cifs=[]))
    with pytest.raises(ValidationError):
        config_io.validate(doc)

    # The caller never reaches save(), so the file is unchanged.
    assert path.read_text(encoding="utf-8") == before


def test_save_is_atomic_replace(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    doc = tomlkit.parse('cif = "12345678"\n')
    config_io.save(doc, path)
    assert path.read_text(encoding="utf-8").strip() == 'cif = "12345678"'
    assert not (tmp_path / "config.toml.tmp").exists()  # temp cleaned up
