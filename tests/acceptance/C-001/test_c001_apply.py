"""C-001 受入テスト。具体例（contract.yaml）と1対1に対応する。ロック後は変更不可。"""
import random
from datetime import date, timedelta

import pytest

from leave.service import LeaveError, LeaveService
from leave.store import Employee, Store

TODAY = date(2026, 12, 1)


def service(balance):
    store = Store()
    store.add(Employee("E001", "テスト", balance))
    return LeaveService(store, today=TODAY), store


def balance(store):
    return store.get("E001").balance


def test_EX_001_one_day_success():
    svc, store = service(5)
    svc.apply("E001", date(2026, 12, 2))
    assert balance(store) == 4


def test_EX_002_no_balance():
    svc, store = service(0)
    with pytest.raises(LeaveError, match="有給残日数が足りません"):
        svc.apply("E001", date(2026, 12, 2))
    assert balance(store) == 0


def test_EX_003_half_day_success():
    svc, store = service(0.5)
    svc.apply("E001", date(2026, 12, 2), amount=0.5)
    assert balance(store) == 0


def test_EX_004_half_left_full_day_rejected():
    svc, store = service(0.5)
    with pytest.raises(LeaveError, match="有給残日数が足りません"):
        svc.apply("E001", date(2026, 12, 2))
    assert balance(store) == 0.5


def test_EX_005_overlap_rejected():
    svc, store = service(5)
    svc.apply("E001", date(2026, 12, 9))
    with pytest.raises(LeaveError, match="申請済みの日と重なっています"):
        svc.apply("E001", date(2026, 12, 8), date(2026, 12, 10))
    assert balance(store) == 4
    assert svc.applied_dates("E001") == {date(2026, 12, 9)}


def test_EX_006_invalid_unit():
    svc, store = service(5)
    with pytest.raises(LeaveError, match="1日または半日で指定してください"):
        svc.apply("E001", date(2026, 12, 2), amount=0.25)
    assert balance(store) == 5


def test_EX_007_period_three_days():
    svc, store = service(5)
    assert svc.apply("E001", date(2026, 12, 7), date(2026, 12, 9)) == 3
    assert balance(store) == 2


def test_EX_008_period_insufficient_no_partial():
    svc, store = service(2)
    with pytest.raises(LeaveError, match="有給残日数が足りません"):
        svc.apply("E001", date(2026, 12, 7), date(2026, 12, 9))
    assert balance(store) == 2
    assert svc.applied_dates("E001") == set()


def test_EX_009_weekend_excluded():
    svc, store = service(5)
    assert svc.apply("E001", date(2026, 12, 4), date(2026, 12, 7)) == 2
    assert balance(store) == 3


def test_EX_010_holiday_excluded():
    svc, store = service(5)
    assert svc.apply("E001", date(2027, 1, 8), date(2027, 1, 12)) == 2
    assert balance(store) == 3


def test_EX_011_past_date_rejected():
    svc, store = service(5)
    with pytest.raises(LeaveError, match="過去の日付は申請できません"):
        svc.apply("E001", date(2026, 11, 30))
    assert balance(store) == 5


def test_invariants_random_sequences():
    """不変条件：残日数は負にならない。不可の申請は状態を変えない。"""
    rng = random.Random(20261201)
    for _ in range(200):
        svc, store = service(rng.choice([0, 0.5, 1, 2, 5, 10]))
        for _ in range(8):
            start = TODAY + timedelta(days=rng.randint(-3, 60))
            end = start + timedelta(days=rng.randint(0, 6)) if rng.random() < 0.5 else None
            amount = rng.choice([1.0, 0.5, 0.25]) if end is None else 1.0
            before = (balance(store), set(svc.applied_dates("E001")))
            try:
                svc.apply("E001", start, end, amount=amount)
            except LeaveError:
                assert (balance(store), svc.applied_dates("E001")) == before
            assert balance(store) >= 0
