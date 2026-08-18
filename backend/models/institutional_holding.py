"""InstitutionalHolding model — a single 13F-HR information-table row, one
position an institutional investment manager reported holding.

13F filings are filed BY the institutional manager (the "filer") ABOUT its
own holdings -- they are not filed by, or indexed against, the companies
being held. This model is deliberately filer-centric (`filer_cik`,
`filer_name` are the primary identity), with `issuer_name`/`cusip`
describing the position -- see `docs/data-model.md` for the full
issuer-vs-filer design discussion and the real constraint this reflects.
"""

from datetime import datetime, date, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field

from .enums import DataStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InstitutionalHolding(SQLModel, table=True):
    """
    A single position row from a 13F-HR information table, filed by an
    institutional investment manager (`filer_cik`/`filer_name`) about a
    holding in `issuer_name` (identified by `cusip`, not a `company_id` FK --
    SEC's 13F data does not carry a CIK for the issuer, only name + CUSIP,
    so linking to an internal `Company` row would require a separate
    CUSIP-to-CIK resolution step this project does not perform; see
    `docs/data-model.md`).
    """

    __tablename__ = "institutional_holding"
    __table_args__ = (
        UniqueConstraint(
            "filer_cik", "accession_number", "cusip", name="uq_institutional_holding_natural_key"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    filer_cik: str = Field(max_length=10, index=True)
    filer_name: Optional[str] = Field(default=None, max_length=256)

    issuer_name: Optional[str] = Field(default=None, max_length=256)
    title_of_class: Optional[str] = Field(default=None, max_length=64)
    cusip: Optional[str] = Field(default=None, max_length=16, index=True)

    period_of_report: Optional[date] = Field(default=None, index=True)
    value: Optional[float] = Field(default=None, description="Reported in thousands of USD per SEC convention")
    shares_or_principal_amount: Optional[float] = Field(default=None)
    shares_or_principal_type: Optional[str] = Field(default=None, max_length=4, description="SH | PRN")
    investment_discretion: Optional[str] = Field(default=None, max_length=8)
    voting_authority_sole: Optional[float] = Field(default=None)
    voting_authority_shared: Optional[float] = Field(default=None)
    voting_authority_none: Optional[float] = Field(default=None)

    accession_number: str = Field(max_length=32, index=True)
    source: str = Field(default="SEC_EDGAR_13F_INFO_TABLE", max_length=64)
    source_url: Optional[str] = Field(default=None, max_length=1024)
    filing_date: Optional[date] = Field(default=None)

    data_status: DataStatus = Field(default=DataStatus.MISSING)

    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)
