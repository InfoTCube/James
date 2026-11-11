from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base, UTCDateTime


class ShoppingItem(Base):
    """One thing on the shopping list."""

    __tablename__ = "shopping_items"

    id: Mapped[int] = mapped_column(primary_key=True)  # reused once the list empties: short
    text: Mapped[str]
    added_at: Mapped[datetime] = mapped_column(UTCDateTime)
