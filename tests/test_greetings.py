"""Birthday and month-birthday greetings: which days, which wording."""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.delivery import greetings as gr  # noqa: E402


def greeted_days(birth, years=4):
    start = date.fromisoformat(birth)
    days = []
    for i in range(1, 366 * years):
        on = start + timedelta(days=i)
        text = gr.greeting("Маша", birth, on)
        if text:
            days.append((on.isoformat(), text))
    return days


class Schedule(unittest.TestCase):
    def test_monthly_in_the_first_year_then_every_half_year(self):
        days = [d for d, _ in greeted_days("2025-05-03")]
        self.assertEqual(days[:12], [f"{y}-{m:02d}-03" for y, m in
                                     [(2025, m) for m in range(6, 13)] + [(2026, m) for m in range(1, 6)]])
        self.assertEqual(days[12:16], ["2026-11-03", "2027-05-03", "2027-11-03", "2028-05-03"])

    def test_wording(self):
        texts = dict(greeted_days("2025-05-03"))
        self.assertIn("Маша, сегодня тебе 1 месяц", texts["2025-06-03"])
        self.assertIn("5 месяцев", texts["2025-10-03"])
        self.assertIn("2 месяца", texts["2025-07-03"])
        self.assertIn("с днём рождения", texts["2026-05-03"].lower())
        self.assertIn("1 год", texts["2026-05-03"])
        self.assertIn("полтора года", texts["2026-11-03"])
        self.assertIn("2 года", texts["2027-05-03"])
        self.assertIn("2 с половиной года", texts["2027-11-03"])
        self.assertIn("5 лет", gr.greeting("Маша", "2025-05-03", "2030-05-03"))

    def test_end_of_month_births_are_greeted_on_the_last_day_of_short_months(self):
        days = [d for d, _ in greeted_days("2025-01-31", years=1)]
        self.assertEqual(days[:3], ["2025-02-28", "2025-03-31", "2025-04-30"])
        self.assertIn("1 месяц", gr.greeting("Маша", "2025-01-31", "2025-02-28"))
        self.assertIsNone(gr.greeting("Маша", "2025-01-31", "2025-03-30"))

    def test_a_leap_day_birthday_is_celebrated_on_28_february(self):
        self.assertIn("1 год", gr.greeting("Маша", "2024-02-29", "2025-02-28"))

    def test_ordinary_days_and_the_birth_day_itself_get_nothing(self):
        self.assertIsNone(gr.greeting("Маша", "2025-05-03", "2025-05-03"))
        self.assertIsNone(gr.greeting("Маша", "2025-05-03", "2025-06-04"))
        self.assertIsNone(gr.greeting("Маша", "2025-05-03", "2026-06-03"))  # 13 months: second year is half-yearly


if __name__ == "__main__":
    unittest.main()
