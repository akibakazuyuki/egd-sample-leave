"""従業員と有給残日数のインメモリ保存。サンプル用。"""
from dataclasses import dataclass, field


@dataclass
class Employee:
    id: str
    name: str
    balance: float  # 有給残日数（0.5日単位）


@dataclass
class Store:
    employees: dict = field(default_factory=dict)

    def add(self, emp: Employee) -> None:
        self.employees[emp.id] = emp

    def get(self, emp_id: str) -> Employee:
        return self.employees[emp_id]


def demo_store() -> Store:
    s = Store()
    s.add(Employee("E001", "佐藤", 5.0))
    s.add(Employee("E002", "鈴木", 0.0))
    s.add(Employee("E003", "高橋", 0.5))
    return s
