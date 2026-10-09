"""`chatbot_file_links` - the short links the chatbot sends instead of files (PHOTO-SEND-CAP).

A contact switched to link mode (`respond_contacts.chatbot_files_as_links`) gets one text
message where each file is a `{frontend_base_url}/f/{code}` link, not a WhatsApp file
each (about 0.042 MYR per message). One row per distinct file per turn: `code` is the
whole capability (16 Crockford characters from `secrets`, 80 bits), so the public
route takes a code and nothing else, never a url. `target_url` is the stored, UNSIGNED
CDN url; a fresh signed url is minted on each click, so the stored one never leaves us.

`expires_at` is stamped at mint time from `system_settings.chatbot_file_link_expiry_days`
(null = never, the owner's choice on 9 Oct 2026). The click columns are the A/B readout:
which turn sent the link, and whether the contact opened it.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class ChatbotFileLink(Base):
    __tablename__ = "chatbot_file_links"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code = Column(String(16), nullable=False, unique=True, index=True)
    target_url = Column(Text, nullable=False)
    filename = Column(Text, nullable=True)
    mime_type = Column(String(255), nullable=True)
    contact_respond_id = Column(String(64), nullable=True, index=True)
    turn_id = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=False), nullable=True)
    click_count = Column(Integer, nullable=False, server_default="0", default=0)
    first_clicked_at = Column(DateTime(timezone=False), nullable=True)
    last_clicked_at = Column(DateTime(timezone=False), nullable=True)
