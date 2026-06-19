"""Pydantic models for the BillToBoxAgent configuration tree.

Loaded from a YAML file (see :mod:`billtobox_agent.config.loader`). Mirrors
HomeEnergyCenter's config approach: a strict, frozen model tree with secrets held
as :class:`pydantic.SecretStr` *inside* the config (never environment variables).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, model_validator

Port = Annotated[int, Field(ge=1, le=65535)]
Month = Annotated[int, Field(ge=1, le=12)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class _StrictModel(BaseModel):
    """Base: reject unknown keys and forbid mutation after construction."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# ----- external service credentials -------------------------------------------


class AnthropicConfig(_StrictModel):
    api_key: SecretStr
    # The model id and SDK specifics are settled in task 13 (extraction).


class GoogleConfig(_StrictModel):
    """Gmail (read-only) and Google Drive (drive.file) — one OAuth client."""

    client_id: str = Field(min_length=1)
    client_secret: SecretStr
    token_path: Path = Path("data/google_token.json")


class MicrosoftConfig(_StrictModel):
    """Outlook / Microsoft 365 mail (Mail.Read) via MSAL."""

    client_id: str = Field(min_length=1)
    # Optional: the headless device-code (public client) flow needs no secret;
    # kept for a possible future confidential-client setup.
    client_secret: SecretStr | None = None
    # 'common' works for both personal (outlook.com) and work/school accounts.
    tenant_id: str = "common"
    token_path: Path = Path("data/ms_token.json")


class DriveConfig(_StrictModel):
    # None => the agent creates the root folder on first run and records its id
    # (decisions.md #9). Set an existing folder id to reuse one.
    root_folder_id: str | None = None
    root_folder_name: str = "Invoices"


class BilltoboxConfig(_StrictModel):
    mailbox_address: EmailStr
    sender_address: EmailStr


class SmtpConfig(_StrictModel):
    """SMTP transport for emailing approved invoices to the Billtobox mailbox."""

    host: str = Field(min_length=1)
    port: Port = 587
    username: str = Field(min_length=1)
    password: SecretStr
    use_tls: bool = True


# ----- behaviour ---------------------------------------------------------------


class ExtractionConfig(_StrictModel):
    confidence_threshold: Confidence = 0.85


class QuarterDateField(StrEnum):
    INVOICE_DATE = "invoice_date"
    RECEIVED_DATE = "received_date"


class AccountingConfig(_StrictModel):
    fiscal_year_start_month: Month = 1
    quarter_date_field: QuarterDateField = QuarterDateField.INVOICE_DATE
    fy_label_prefix: str = ""


class Source(StrEnum):
    GMAIL = "gmail"
    OUTLOOK = "outlook"
    DOCCLE = "doccle"


class SourcesConfig(_StrictModel):
    polling: tuple[Source, ...] = (Source.GMAIL, Source.OUTLOOK)

    @model_validator(mode="after")
    def _at_least_one_source(self) -> SourcesConfig:
        if not self.polling:
            raise ValueError("sources.polling must list at least one source")
        return self


class StorageConfig(_StrictModel):
    sqlite_path: Path = Path("data/billtobox.db")


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LoggingConfig(_StrictModel):
    log_dir: Path = Path("logs")
    level: LogLevel = "INFO"
    retention_days: int = Field(default=30, ge=1)


class WebConfig(_StrictModel):
    # Bound to localhost; Caddy fronts TLS on the public port (CONVENTIONS.md §11).
    host: str = "127.0.0.1"
    port: Port = 9003


# ----- root --------------------------------------------------------------------


class AppConfig(_StrictModel):
    # Required: external credentials have no safe default.
    anthropic: AnthropicConfig
    google: GoogleConfig
    microsoft: MicrosoftConfig
    billtobox: BilltoboxConfig
    smtp: SmtpConfig
    # Optional: sensible defaults (decisions.md).
    drive: DriveConfig = Field(default_factory=DriveConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    accounting: AccountingConfig = Field(default_factory=AccountingConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    web: WebConfig = Field(default_factory=WebConfig)
