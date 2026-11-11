from datetime import date

from sqlalchemy.orm import Mapped, mapped_column

from assistant.core.db import Base


class MealIdea(Base):
    """One of the week's dinner ideas."""

    __tablename__ = "meals_ideas"

    week: Mapped[date] = mapped_column(primary_key=True)  # its Monday
    idx: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    uses: Mapped[str]  # the promo products it uses
    how: Mapped[str]  # one sentence
