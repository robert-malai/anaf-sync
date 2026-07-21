"""Round-trip ``config.toml`` edits while preserving the user's file verbatim.

The tray must never clobber the comments and layout a user hand-wrote. tomlkit
parses the file into a document that remembers its formatting; :func:`apply`
mutates *only* the keys the form actually changed, and :func:`save` writes it
back atomically. Validation runs against the real :class:`SyncConfig` before any
write, so an invalid edit leaves the file on disk untouched. Pure and heavily
tested — no Qt here.
"""

from __future__ import annotations

import os
from pathlib import Path

import tomlkit
from pydantic import BaseModel, field_validator

from ..config import SyncConfig

__all__ = ["SettingsForm", "apply", "load", "save", "validate"]


class SettingsForm(BaseModel):
    """The editable subset of ``config.toml``, as the Settings form holds it.

    A plain container — validation is delegated to :class:`SyncConfig` via
    :func:`validate`, so the tray and the CLI never disagree on what is legal.
    """

    cifs: list[str]
    direction: str
    lookback_days: int
    directory: str
    template: str
    artifacts: list[str]

    @field_validator("directory")
    @classmethod
    def _forward_slashes(cls, value: str) -> str:
        r"""Canonicalise separators so a Windows round-trip stays a no-op.

        The form holds the directory as text, but it arrives as `str(Path(...))`
        from the parsed config — which is `~\Facturi` on Windows. Left alone
        that reads as a changed value and rewrites a key the user never touched,
        breaking the minimal-diff contract :func:`apply` exists to keep. Qt's
        file dialog returns forward slashes on every platform and pathlib
        accepts them on Windows, so forward slashes are the canonical form.
        """
        return value.replace("\\", "/")


def load(path: Path) -> tomlkit.TOMLDocument:
    """Parse ``config.toml`` into a formatting-preserving document."""
    return tomlkit.parse(path.read_text(encoding="utf-8"))


def apply(doc: tomlkit.TOMLDocument, form: SettingsForm) -> None:
    """Write the form's values into ``doc``, touching only changed keys.

    Unchanged keys are left exactly as they were (same value object, same
    surrounding comments and whitespace), so a save that changes one field
    produces a one-line diff.
    """
    _set_cifs(doc, form.cifs)
    _set_if_changed(doc, "direction", form.direction)
    _set_if_changed(doc, "lookback_days", form.lookback_days)

    output = doc.get("output")
    if output is None:
        output = tomlkit.table()
        doc["output"] = output
    _set_if_changed(output, "directory", form.directory)
    _set_if_changed(output, "template", form.template)
    _set_if_changed(output, "artifacts", form.artifacts)


def validate(doc: tomlkit.TOMLDocument) -> SyncConfig:
    """Validate the document as a :class:`SyncConfig`; raises on invalid input.

    Raises:
        pydantic.ValidationError: the edited config is not valid.
    """
    return SyncConfig.model_validate(doc.unwrap())


def save(doc: tomlkit.TOMLDocument, path: Path) -> None:
    """Write ``doc`` back atomically (temp file + ``os.replace`` in the dir)."""
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    os.replace(tmp, path)


def _set_if_changed(table: object, key: str, value: object) -> None:
    # tomlkit tables support __contains__/__getitem__/__setitem__; comparing the
    # unwrapped current value avoids reformatting a key that did not change.
    current = table.get(key)  # type: ignore[attr-defined]
    if current is not None and _unwrap(current) == value:
        return
    table[key] = value  # type: ignore[index]


def _set_cifs(doc: tomlkit.TOMLDocument, cifs: list[str]) -> None:
    # The config accepts either `cif = "…"` (single) or `cifs = [...]`. Keep the
    # user's shape when we can; switch to the list form only when we must.
    if "cifs" in doc or len(cifs) != 1:
        _set_if_changed(doc, "cifs", cifs)
        if "cif" in doc:
            del doc["cif"]
    else:
        _set_if_changed(doc, "cif", cifs[0])


def _unwrap(value: object) -> object:
    unwrap = getattr(value, "unwrap", None)
    return unwrap() if callable(unwrap) else value
