"""InsiderTransaction model — a single Form 3/4/5 transaction row about a
company's insider (officer/director/10% owner), from SEC's real ownership
XML documents (see `backend.data.ownership_xml`)."""

from datetime import datetime, date, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field, Relationship

from .enums import DataStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InsiderTransaction(SQLModel, table=True):
    """
    A single reported Form 3/4/5 transaction, scoped to the issuer
    (`company_id`) the transaction concerns. `reporting_owner_*` identifies
    the insider (officer/director/10% owner) who filed it; `is_officer` /
    `is_director` / `is_ten_percent_owner` / `is_other` capture their
    relationship to the issuer, which can be multiple simultaneously (e.g.
    an officer who is also a director) -- modeled as independent booleans
    rather than a single enum, matching the real XML shape.

    Every field is REPORTED-if-present, MISSING-if-absent from the source
    XML (see `data_status`) -- non-derivative and derivative transaction
    rows carry different field sets in real filings (e.g. derivative rows
    add exercise price/expiration date fields not modeled here yet), so a
    field's absence in a given filing is expected, not an error.
    """

    __tablename__ = "insider_transaction"
    __table_args__ = (
        UniqueConstraint(
            "accession_number",
            "reporting_owner_cik",
            "security_title",
            "transaction_date",
            name="uq_insider_transaction_natural_key",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True, nullable=False, description="The issuer")

    reporting_owner_name: Optional[str] = Field(default=None, max_length=256)
    reporting_owner_cik: Optional[str] = Field(default=None, max_length=10, index=True)
    is_officer: Optional[bool] = Field(default=None)
    is_director: Optional[bool] = Field(default=None)
    is_ten_percent_owner: Optional[bool] = Field(default=None)
    is_other: Optional[bool] = Field(default=None)
    officer_title: Optional[str] = Field(default=None, max_length=128)

    transaction_table: str = Field(default="nonDerivative", max_length=16, description="nonDerivative | derivative")
    security_title: Optional[str] = Field(default=None, max_length=256)
    transaction_date: Optional[date] = Field(default=None, index=True)
    transaction_code: Optional[str] = Field(default=None, max_length=4)
    shares_transacted: Optional[float] = Field(default=None)
    price_per_share: Optional[float] = Field(default=None)
    transaction_acquired_disposed_code: Optional[str] = Field(default=None, max_length=1)
    shares_owned_after: Optional[float] = Field(default=None)
    ownership_type: Optional[str] = Field(default=None, max_length=1, description="D (direct) | I (indirect)")

    accession_number: str = Field(max_length=32, index=True)
    source: str = Field(default="SEC_EDGAR_OWNERSHIP_XML", max_length=64)
    source_url: Optional[str] = Field(default=None, max_length=1024)
    filing_date: Optional[date] = Field(default=None)

    data_status: DataStatus = Field(default=DataStatus.MISSING)

    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)

    company: "Company" = Relationship()
