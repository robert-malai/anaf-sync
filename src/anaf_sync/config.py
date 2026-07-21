"""Configuration: a TOML file for sync behaviour, environment variables for auth.

Credentials deliberately reuse anafpy's own conventions (``ANAFPY_CLIENT_ID``,
``ANAFPY_CLIENT_SECRET``, ``ANAFPY_TOKEN_STORE``, ``ANAFPY_TOKEN_STORE_BACKEND``)
so the login performed with ``anafpy auth login`` — CLI or MCP server — is shared
as-is. Everything behavioural (CIFs, window, output template, artifacts) lives in
a TOML file the user owns.
"""

from __future__ import annotations

import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

import platformdirs
from anafpy.auth import FileTokenStore, KeyringTokenStore, TokenProvider, TokenStore
from anafpy.exceptions import AnafConfigError
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = [
    "Artifact",
    "AuthSettings",
    "Direction",
    "OutputConfig",
    "SyncConfig",
    "default_config_path",
    "default_state_path",
    "load_config",
    "write_default_config",
]

APP_NAME = "anaf-sync"


def default_config_path() -> Path:
    """``config.toml`` in the platform config dir (roaming on Windows)."""
    return platformdirs.user_config_path(APP_NAME, appauthor=False) / "config.toml"


def default_env_path() -> Path:
    """``.env`` next to the config file — the location scheduled runs can find.

    A CWD-relative ``.env`` works interactively but not under Task Scheduler,
    systemd, or launchd, whose working directory is not the project folder.
    """
    return default_config_path().with_name(".env")


def default_state_path() -> Path:
    """``state.db`` in the platform state dir (survives config wipes)."""
    return platformdirs.user_state_path(APP_NAME, appauthor=False) / "state.db"


_DEFAULT_TEMPLATE = (
    "{cif}/{direction}/{issue_date:%Y}/{issue_date:%m}/"
    "{issue_date:%Y-%m-%d}_{number}_{partner_name}"
)


class Direction(StrEnum):
    """Which side of the exchange to archive."""

    RECEIVED = "received"
    SENT = "sent"
    BOTH = "both"


class Artifact(StrEnum):
    """What to write to disk for each downloaded message."""

    ZIP = "zip"  # the raw descarcare ZIP — the legally meaningful archive
    XML = "xml"  # the invoice UBL XML extracted from the ZIP
    SIGNATURE = "signature"  # the detached MF signature XML
    PDF = "pdf"  # ANAF's own PDF rendering (public transformare service)
    METADATA = "metadata"  # a small JSON sidecar with the message + summary


class OutputConfig(BaseModel):
    """Where and how downloaded invoices are written."""

    directory: Path = Path("~/Facturi")
    template: str = _DEFAULT_TEMPLATE
    artifacts: list[Artifact] = [Artifact.ZIP, Artifact.PDF]

    @field_validator("artifacts")
    @classmethod
    def _non_empty(cls, value: list[Artifact]) -> list[Artifact]:
        if not value:
            raise ValueError("output.artifacts cannot be empty")
        return value

    @property
    def resolved_directory(self) -> Path:
        return self.directory.expanduser()


class SyncConfig(BaseModel):
    """The TOML configuration file, validated."""

    cifs: list[str] = Field(min_length=1)
    direction: Direction = Direction.RECEIVED
    lookback_days: int = Field(default=60, ge=1, le=60)
    # Only failure traces are pruned (observability-only); downloaded records
    # are the permanent catalog, so no floor is needed to protect them.
    failure_retention_days: int = Field(default=90, ge=1)
    output: OutputConfig = OutputConfig()

    @model_validator(mode="before")
    @classmethod
    def _accept_single_cif(cls, data: object) -> object:
        # `cif = "123"` is friendlier than a one-element list; accept both.
        if isinstance(data, dict) and "cif" in data and "cifs" not in data:
            data = dict(data)
            data["cifs"] = [data.pop("cif")]
        return data

    @field_validator("cifs")
    @classmethod
    def _digits_only(cls, value: list[str]) -> list[str]:
        cleaned = [str(cif).strip().upper().removeprefix("RO") for cif in value]
        for cif in cleaned:
            if not cif.isdigit():
                raise ValueError(f"CIF {cif!r} is not numeric (drop any RO prefix)")
        return cleaned


class AuthSettings(BaseSettings):
    """ANAF OAuth credentials and token-store location, from ``ANAFPY_*`` env vars.

    Mirrors anafpy's own MCP-server config so one login serves every consumer.
    Env files hold the same ``ANAFPY_*`` variables: the config-dir ``.env`` is
    the one scheduled runs (undefined CWD) can find, a CWD ``.env`` wins over
    it interactively, and real environment variables beat both.
    """

    model_config = SettingsConfigDict(
        env_file=(default_env_path(), ".env"), extra="ignore"
    )

    client_id: str | None = Field(default=None, validation_alias="ANAFPY_CLIENT_ID")
    client_secret: str | None = Field(
        default=None, validation_alias="ANAFPY_CLIENT_SECRET"
    )
    token_store_path: Path = Field(
        default=Path("~/.anafpy/tokens.json"), validation_alias="ANAFPY_TOKEN_STORE"
    )
    token_store_backend: Literal["keyring", "file"] = Field(
        default="keyring", validation_alias="ANAFPY_TOKEN_STORE_BACKEND"
    )

    def build_store(self) -> TokenStore:
        if self.token_store_backend == "file":
            return FileTokenStore(self.token_store_path)
        return KeyringTokenStore()

    def build_provider(self) -> TokenProvider:
        """A refreshing token provider over the shared store.

        Raises:
            AnafConfigError: the OAuth client credentials are missing — without
                them expired access tokens cannot be refreshed, which a scheduled
                job depends on.
        """
        if not self.client_id or not self.client_secret:
            raise AnafConfigError(
                "ANAFPY_CLIENT_ID / ANAFPY_CLIENT_SECRET are not set — anaf-sync "
                "needs them to refresh tokens between scheduled runs"
            )
        return TokenProvider(
            client_id=self.client_id,
            client_secret=self.client_secret,
            store=self.build_store(),
        )

    @classmethod
    def from_env(cls) -> Self:
        return cls()


def load_config(path: Path) -> SyncConfig:
    """Load and validate the TOML configuration.

    Raises:
        FileNotFoundError: no config file at ``path`` — run ``anaf-sync init``.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"no configuration at {path} — run `anaf-sync init` to create one"
        )
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return SyncConfig.model_validate(data)


_DEFAULT_CONFIG_TOML = f"""\
# anaf-sync configuration.
#
# Credentials are NOT stored here: anaf-sync reuses the anafpy login
# (`anafpy auth login`) plus the ANAFPY_CLIENT_ID / ANAFPY_CLIENT_SECRET
# environment variables, exactly like the anafpy MCP server.

# The company CIF(s) to archive invoices for (numeric, no RO prefix).
cif = "12345678"
# ...or several:  cifs = ["12345678", "87654321"]

# Which invoices to download: "received", "sent", or "both".
direction = "received"

# How far back each run looks (1-60; ANAF purges messages after 60 days).
lookback_days = 60

# How long to keep failure traces of messages that keep failing to download,
# in days. These are shown by `anaf-sync status` so a persistent failure is
# visible before ANAF's 60-day window closes on it; older ones are pruned at
# the start of each run. Archived-message records are kept forever (the archive
# is a permanent catalog) and are never affected by this.
failure_retention_days = 90

[output]
# Root folder for the archive (created if missing; ~ is expanded).
directory = "~/Facturi"

# Where each invoice lands, relative to `directory`, as a template over the
# invoice's context variables. Python format-spec syntax applies, so dates
# support strftime specs like {{issue_date:%Y}}. Available variables:
#
#   number          invoice number (BT-1)
#   issue_date      issue date (a real date - format with strftime specs)
#   issue_month     Romanian month name of the issue date ("iulie")
#   due_date        due date, when present
#   currency        invoice currency code
#   kind            "invoice" or "credit_note"
#   direction       "received" or "sent"
#   cif             the CIF this sync run queried
#   partner_name    the other party's name   partner_cif   the other party's CIF
#   message_id      ANAF download id         request_id    ANAF upload id
#   message_type    ANAF message type        created       message creation time
#   created_month   Romanian month name of the creation time
#
# Everything above except cif, direction, message_id, request_id and
# message_type comes from the invoice XML, so it renders "unknown" for messages
# that carry none (error files, buyer messages); created / created_month come
# from ANAF's listing and can (rarely) be "unknown" too. Build a template that
# still separates those, not one that collapses them onto each other.
#
# Values are sanitised for the filesystem; "/" in the template creates folders.
# Do not add an extension - each artifact appends its own (.zip, .xml, ...).
template = "{_DEFAULT_TEMPLATE}"

# What to save per invoice: "zip" (raw signed archive), "xml" (invoice UBL),
# "signature" (detached MF signature), "pdf" (ANAF's rendering), "metadata"
# (JSON sidecar with the message details).
artifacts = ["zip", "pdf"]
"""


def write_default_config(path: Path, *, force: bool = False) -> Path:
    """Write the commented default config to ``path``.

    Raises:
        FileExistsError: the file exists and ``force`` is not set.
    """
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_DEFAULT_CONFIG_TOML, encoding="utf-8")
    return path
