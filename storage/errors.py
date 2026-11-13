"""存储层异常类型。"""


class StorageError(Exception):
    """存储层基础异常。"""


class DuplicateError(StorageError):
    """唯一约束冲突（如重复消息、同一候选人同一岗位的重复应聘记录）。"""


class NotFoundError(StorageError):
    """按标识查询时未找到对应记录。"""
