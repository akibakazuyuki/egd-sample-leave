"""日本の国民の祝日（振替休日・国民の休日を含む）。

出典：内閣府「国民の祝日について」https://www8.cao.go.jp/chosei/shukujitsu/gaiyou.html
（2026年・2027年分、2026-11-25確認）。範囲外の年は COVERED_YEARS で判定する。
"""
from datetime import date

COVERED_YEARS = range(2026, 2028)

HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 12), date(2026, 2, 11), date(2026, 2, 23),
    date(2026, 3, 20), date(2026, 4, 29), date(2026, 5, 3), date(2026, 5, 4),
    date(2026, 5, 5), date(2026, 5, 6), date(2026, 7, 20), date(2026, 8, 11),
    date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23), date(2026, 10, 12),
    date(2026, 11, 3), date(2026, 11, 23),
    date(2027, 1, 1), date(2027, 1, 11), date(2027, 2, 11), date(2027, 2, 23),
    date(2027, 3, 21), date(2027, 3, 22), date(2027, 4, 29), date(2027, 5, 3),
    date(2027, 5, 4), date(2027, 5, 5), date(2027, 7, 19), date(2027, 8, 11),
    date(2027, 9, 20), date(2027, 9, 23), date(2027, 10, 11), date(2027, 11, 3),
    date(2027, 11, 23),
}


def is_day_off(d: date) -> bool:
    return d.weekday() >= 5 or d in HOLIDAYS
