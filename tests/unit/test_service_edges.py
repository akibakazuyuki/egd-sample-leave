from datetime import date

import pytest

from leave.service import LeaveError, LeaveService
from leave.store import Employee, Store


def svc(balance=5):
    s = Store()
    s.add(Employee("E", "t", balance))
    return LeaveService(s, today=date(2026, 12, 1)), s


def test_weekend_only_rejected():
    v, _ = svc()
    with pytest.raises(LeaveError, match="休日だけ"):
        v.apply("E", date(2026, 12, 5), date(2026, 12, 6))


def test_end_before_start_rejected():
    v, _ = svc()
    with pytest.raises(LeaveError, match="終了日"):
        v.apply("E", date(2026, 12, 9), date(2026, 12, 8))


def test_half_day_in_range_rejected():
    v, _ = svc()
    with pytest.raises(LeaveError, match="半日は1日だけ"):
        v.apply("E", date(2026, 12, 7), date(2026, 12, 8), amount=0.5)


def test_outside_holiday_data_rejected():
    v, _ = svc()
    with pytest.raises(LeaveError, match="祝日情報がない"):
        v.apply("E", date(2028, 1, 5))


def test_today_is_allowed():
    v, s = svc()
    v.apply("E", date(2026, 12, 1))
    assert s.get("E").balance == 4


def test_today_is_reevaluated_each_call():
    days = iter([date(2026, 12, 1), date(2026, 12, 3)])
    s = Store()
    s.add(Employee("E", "t", 5))
    v = LeaveService(s, today=lambda: next(days))
    v.apply("E", date(2026, 12, 2))                 # 12/1時点では未来
    with pytest.raises(LeaveError, match="過去"):
        v.apply("E", date(2026, 12, 2))             # 12/3時点では過去
