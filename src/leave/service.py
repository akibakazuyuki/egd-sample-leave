"""有給休暇の申請（C-001）。"""
from datetime import date, timedelta
from typing import Optional

from leave.holidays import COVERED_YEARS, is_day_off


class LeaveError(Exception):
    """申請が受け付けられない理由（利用者に見せる文言）。"""


class LeaveService:
    def __init__(self, store, today: date):
        self.store = store
        self.today = today
        self._applied = {}  # emp_id -> {date: amount}

    def apply(self, emp_id: str, start: date, end: Optional[date] = None, amount: float = 1.0):
        """有給を申請する。受け付けた日数を返し、不可ならLeaveErrorを送出する。

        検査をすべて終えてから状態を変えるので、不可の申請は何も変えない。
        """
        emp = self.store.get(emp_id)
        end = end or start
        if end < start:
            raise LeaveError("終了日は開始日以降にしてください")
        if start != end and amount != 1.0:
            raise LeaveError("半日は1日だけの申請で使えます")
        if amount not in (1.0, 0.5):
            raise LeaveError("1日または半日で指定してください")
        if start < self.today:
            raise LeaveError("過去の日付は申請できません")
        if start.year not in COVERED_YEARS or end.year not in COVERED_YEARS:
            raise LeaveError("この期間の祝日情報がないため申請できません")

        days = [start + timedelta(n) for n in range((end - start).days + 1)]
        workdays = [d for d in days if not is_day_off(d)]
        if not workdays:
            raise LeaveError("休日だけの申請はできません")
        applied = self._applied.setdefault(emp_id, {})
        if any(d in applied for d in workdays):
            raise LeaveError("申請済みの日と重なっています")
        total = amount * len(workdays)
        if total > emp.balance:
            raise LeaveError("有給残日数が足りません")

        for d in workdays:
            applied[d] = amount
        emp.balance -= total
        return total

    def applied_dates(self, emp_id: str) -> set:
        return set(self._applied.get(emp_id, {}))
