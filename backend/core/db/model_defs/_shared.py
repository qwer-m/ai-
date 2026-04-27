from sqlalchemy import Column, Integer, String, Boolean, JSON, DateTime, ForeignKey, Float, Text, func, UniqueConstraint
from sqlalchemy.orm import relationship, backref
from sqlalchemy.dialects.mysql import LONGTEXT
from core.db.database import Base
