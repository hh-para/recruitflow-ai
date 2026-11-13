"""RecruitFlow 存储层：SQLite 数据模型与持久化访问。

对外暴露连接、建表、仓储（Repository）与异常类型。
本层只负责持久化与查询，不包含业务规则。
"""
from .db import connect, init_db, SCHEMA
from .errors import StorageError, DuplicateError, NotFoundError
from .repository import LedgerRepository, CLEAR

__all__ = [
    "connect",
    "init_db",
    "SCHEMA",
    "StorageError",
    "DuplicateError",
    "NotFoundError",
    "LedgerRepository",
    "CLEAR",
]
