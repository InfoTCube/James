from datetime import date, datetime

from sqlalchemy import Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class BiedronkaProduct(Base):
    """Something you buy, watched for in the leaflets. Set from Telegram (/buy)."""

    __tablename__ = "biedronka_products"

    key: Mapped[str] = mapped_column(primary_key=True)  # name, case and diacritics folded
    name: Mapped[str]
    added_at: Mapped[datetime] = mapped_column(UTCDateTime)


class BiedronkaCheck(Base):
    """Claude's verdict on one offer OCR found: really the product, at that price?"""

    __tablename__ = "biedronka_checks"

    key: Mapped[str] = mapped_column(primary_key=True)  # service.offer_key
    ok: Mapped[bool]  # False: not the product, or not its price; hidden
    price: Mapped[float | None]  # the right price, when Claude saw a different one nearby


class BiedronkaPage(Base):
    """One page of a current grocery leaflet. Removed when the leaflet leaves the website."""

    __tablename__ = "biedronka_pages"
    __table_args__ = (UniqueConstraint("press_id", "image"),)  # leaflets can share a page

    id: Mapped[int] = mapped_column(primary_key=True)
    press_id: Mapped[str] = mapped_column(index=True)  # the leaflet's id on biedronka.pl
    image: Mapped[str]  # page image URL
    # OCR: JSON [[x, y, height, text]]; None = not read yet
    lines: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[date | None]  # as printed on the page, if OCR could read it
    valid_to: Mapped[date | None]
