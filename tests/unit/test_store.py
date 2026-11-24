from leave.store import demo_store


def test_demo_store_has_three_employees():
    assert len(demo_store().employees) == 3
