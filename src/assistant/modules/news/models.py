from datetime import date, datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class NewsItem(Base):
    """A headline from one of the configured feeds, kept a few days."""

    __tablename__ = "news_items"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # sha1 of the link
    feed: Mapped[str]  # its name in config
    title: Mapped[str]
    link: Mapped[str]
    published: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


class NewsDigest(Base):
    """The AI-written digest of one day's headlines, made overnight."""

    __tablename__ = "news_digests"

    day: Mapped[date] = mapped_column(primary_key=True)  # local date it's for (the morning)
    text: Mapped[str] = mapped_column(Text)  # "• ..." lines
    made_at: Mapped[datetime] = mapped_column(UTCDateTime)
