from datetime import date

from leave.holidays import HOLIDAYS, is_day_off


def test_known_holidays():
    assert date(2026, 11, 23) in HOLIDAYS          # 勤労感謝の日
    assert date(2027, 3, 22) in HOLIDAYS           # 振替休日
    assert len([d for d in HOLIDAYS if d.year == 2026]) == 18
    assert len([d for d in HOLIDAYS if d.year == 2027]) == 17


def test_weekend_is_day_off():
    assert is_day_off(date(2026, 12, 5))           # 土
    assert not is_day_off(date(2026, 12, 4))       # 金
