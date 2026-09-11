from __future__ import annotations

from datetime import datetime
from typing import Optional

import pymongo
from pydantic import EmailStr

from .common import Role, TimestampedDocument


class User(TimestampedDocument):
    email: EmailStr
    password_hash: str
    role: Role
    first_name: str
    last_name: str
    phone_number: str
    is_active: bool = True
    last_login: Optional[datetime] = None

    class Settings:
        name = "users"
        indexes = [
            pymongo.IndexModel([("email", pymongo.ASCENDING)], unique=True),
        ]
