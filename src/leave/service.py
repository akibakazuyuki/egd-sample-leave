"""有給休暇の申請。C-001で実装する。"""
from datetime import date
from typing import Optional


class LeaveError(Exception):
    """申請が受け付けられない理由（利用者に見せる文言）。"""


class LeaveService:
    def __init__(self, store, today: date):
        self.store = store
        self.today = today

    def apply(self, emp_id: str, start: date, end: Optional[date] = None, amount: float = 1.0):
        """有給を申請する。受け付けた日数を返し、不可ならLeaveErrorを送出する。"""
        raise NotImplementedError

    def applied_dates(self, emp_id: str) -> set:
        raise NotImplementedError
