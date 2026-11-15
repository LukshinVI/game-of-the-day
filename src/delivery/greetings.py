"""Birthday, "month-birthday" and holiday greetings, prepended to the game of that day.

Rules (deterministic, no model):
- first year: every month on the day of birth (1..11 months);
- every full year: a birthday greeting;
- from the second year on: also every half year (1.5, 2.5, ... years).

A child born on the 29th-31st is greeted on the last day of shorter months.
The child is addressed by name in the nominative ("Маша, сегодня тебе ..."), so
names never need declension.

Holidays (off unless GAME_HOLIDAYS=1): New Year on 1 January, Mother's Day on the last
Sunday of November and Father's Day on the third Sunday of October, the Russian dates.
A holiday greeting speaks to all mums or dads, never assumes who reads the chat.
"""

import calendar
import os
from datetime import date, timedelta

MONTHLY = [
    "🎉 {name}, сегодня тебе {age}! Целый месяц новых открытий — поздравляем 💛",
    "🎉 {name}, тебе уже {age}! Растёшь не по дням, а по часам 🌱",
    "🎉 {name}, сегодня ровно {age}! Ещё один месяц улыбок и маленьких побед ✨",
    "🎉 {name}, с днём рождения в миниатюре: тебе {age}! Обнимаем 🤗",
]
HALF = [
    "🎈 {name}, сегодня тебе {age}! Полгода пролетели — поздравляем с половинкой дня рождения 💛",
    "🎈 {name}, тебе {age}! Столько всего уже умеешь — гордимся 🌟",
]
BIRTHDAY = [
    "🎂 {name}, с днём рождения! Сегодня тебе {age}! Пусть каждый день будет полон игр, смеха и открытий 🎁",
    "🎂 С днём рождения, {name}! Тебе {age} — какой большой путь за этот год. Любим и поздравляем 🎉",
]


def plural(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def is_monthly_anniversary(birth, on):
    """True when `on` is the day-of-birth in its month (or the month's last day if shorter)."""
    last_day = calendar.monthrange(on.year, on.month)[1]
    return on.day == min(birth.day, last_day)


def months_between(birth, on):
    months = (on.year - birth.year) * 12 + on.month - birth.month
    return months - (1 if on.day < min(birth.day, calendar.monthrange(on.year, on.month)[1]) else 0)


def greeting(name, birth_iso, on_date):
    """The greeting for `on_date`, or None on an ordinary day."""
    birth = date.fromisoformat(birth_iso)
    on = date.fromisoformat(on_date) if isinstance(on_date, str) else on_date
    if on <= birth or not is_monthly_anniversary(birth, on):
        return None
    months = months_between(birth, on)
    years, rest = divmod(months, 12)

    if years == 0:
        age = f"{months} {plural(months, 'месяц', 'месяца', 'месяцев')}"
        template = MONTHLY[(months - 1) % len(MONTHLY)]
    elif rest == 0:
        age = f"{years} {plural(years, 'год', 'года', 'лет')}"
        template = BIRTHDAY[(years - 1) % len(BIRTHDAY)]
    elif rest == 6:
        age = "полтора года" if years == 1 else f"{years} с половиной {plural(years, 'год', 'года', 'лет')}"
        template = HALF[(years - 1) % len(HALF)]
    else:
        return None
    return template.format(name=name, age=age)


HOLIDAYS = {
    "new_year": (
        "🎄 С Новым годом!\n\n"
        "Для маленького человека каждый день — большое приключение, а впереди ещё целый год "
        "удивительных открытий!\n\n"
        "Пусть в доме будет тепло, в сердцах — радость, а в семейной копилке воспоминаний — "
        "всё больше счастливых моментов.\n\n"
        "Пусть сбываются мечты и больших, и маленьких!\n\n"
        "С теплом, «Игра дня» 🤍"
    ),
    "mothers_day": (
        "🌷 Сегодня особенный праздник — День матери.\n\n"
        "В маленьких ладошках, первых улыбках и крепких объятиях живёт такая большая любовь.\n\n"
        "Пусть каждая мама будет окружена нежностью, вниманием и заботой. Пусть рядом всегда будут "
        "те, кто поддержит, обнимет и напомнит, как сильно её любят.\n\n"
        "Пусть сегодня в каждом доме станет чуточку теплее.\n\n"
        "С теплом, «Игра дня» 🤍"
    ),
    "fathers_day": (
        "🍀 Сегодня особенный праздник — День отца.\n\n"
        "Как здорово, когда есть с кем построить самую высокую башню, отправиться навстречу "
        "приключениям и превратить обычный день в маленькую сказку.\n\n"
        "Пусть у каждого папы будет больше времени для таких моментов, искренних улыбок и крепких "
        "детских объятий.\n\n"
        "Ведь самые большие приключения начинаются с маленьких шагов вместе.\n\n"
        "С теплом, «Игра дня» 🤍"
    ),
}


def _nth_sunday(year, month, n):
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _last_sunday(year, month):
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - 6) % 7)


def holiday(on_date):
    """The holiday key for `on_date` ('new_year', 'mothers_day', 'fathers_day') or None."""
    if os.environ.get("GAME_HOLIDAYS") != "1":
        return None
    on = date.fromisoformat(on_date) if isinstance(on_date, str) else on_date
    if (on.month, on.day) == (1, 1):
        return "new_year"
    if on == _last_sunday(on.year, 11):
        return "mothers_day"
    if on == _nth_sunday(on.year, 10, 3):
        return "fathers_day"
    return None
