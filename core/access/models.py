"""Database models for delegated userbot access."""

from sqlalchemy import BigInteger, PrimaryKeyConstraint, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Sudoer(Base):
    __tablename__ = "sudoers"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)


class ShareUser(Base):
    __tablename__ = "share_users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)


class IgnoreChat(Base):
    __tablename__ = "ignored_chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)


class AllowedCommand(Base):
    __tablename__ = "allowed_cmds"
    __table_args__ = (PrimaryKeyConstraint("user_id", "command"),)

    user_id: Mapped[int] = mapped_column(BigInteger)
    command: Mapped[str] = mapped_column(Text)
