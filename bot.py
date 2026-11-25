"""
Period. — бот-календарь цикла для себя и подруг.

Стек: Python 3.11+, aiogram 3, SQLite, данные каждой девушки зашифрованы (см. storage.py).

Правила приватности, которые соблюдает код:
  • каждая видит только свой календарь — общих списков и «режима админа» нет;
  • имя, ник и фото не сохраняются — только номер чата и зашифрованный блок данных;
  • содержимое сообщений не пишется в лог, резервных копий нет;
  • /delete стирает всё сразу и навсегда.
"""

import asyncio
import calendar
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from dotenv import load_dotenv

import cycle as C
import texts as T
from storage import Store

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CRYPT_KEY = os.getenv("CRYPT_KEY", "")
INVITE_CODE = os.getenv("INVITE_CODE", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DB_PATH = os.getenv("DB_PATH", "data/cycle.db")

store: Store  # создаётся в main()
BOT_USERNAME = ""

router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)

# Часовой пояс определяем по вопросу «Сколько у тебя сейчас времени?»:
# на каждой кнопке — текущее время в одном из поясов, девушка нажимает то, что видит на часах.
# По одному поясу на каждое смещение от UTC.
TIMEZONES = [
    "Europe/London",       # UTC+0 (летом +1)
    "Europe/Berlin",       # UTC+1 (летом +2)
    "Europe/Kaliningrad",  # UTC+2
    "Europe/Moscow",       # UTC+3
    "Europe/Samara",       # UTC+4
    "Asia/Yekaterinburg",  # UTC+5
    "Asia/Omsk",           # UTC+6
    "Asia/Novosibirsk",    # UTC+7
    "Asia/Irkutsk",        # UTC+8
    "Asia/Yakutsk",        # UTC+9
    "Asia/Vladivostok",    # UTC+10
    "Asia/Magadan",        # UTC+11
    "Asia/Kamchatka",      # UTC+12
]


def tz_label(tz: str) -> str:
    """«UTC+3» — для экрана настроек."""
    offset = datetime.now(ZoneInfo(tz)).utcoffset()
    hours = int(offset.total_seconds() // 3600)
    return f"UTC{hours:+d}" if hours else "UTC"


def tz_now(tz: str) -> str:
    return datetime.now(ZoneInfo(tz)).strftime("%H:%M")

MONTHS_GEN = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
              "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

BTN_TODAY = "📍 Сегодня"
BTN_CAL = "📅 Календарь"
BTN_STATS = "📊 Статистика"
BTN_SET = "⚙️ Настройки"

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_CAL)],
              [KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_SET)]],
    resize_keyboard=True,
)

MARK_EMOJI = {
    C.MARK_PERIOD: "🔴",
    C.MARK_PERIOD_PRED: "⭕",
    C.MARK_OVULATION: "🥚",
    C.MARK_FERTILE: "🟡",
}
LEGEND = "🔴 месячные · ⭕ прогноз месячных\n🟡 можно забеременеть · 🥚 овуляция · 📍 сегодня"


# ───────────────────────── Данные девушки ─────────────────────────

def new_user() -> dict:
    return {
        "step": "privacy",        # privacy → start → plen → clen → mode → time → tz → done
        "await": None,            # ждём ввод текста в настройках (например, длину цикла)
        "tz": "Europe/Moscow",
        "time": "09:00",
        "mode": "track",
        "tips": True,
        "mood": True,
        "fertile": True,
        "discreet": False,
        "clen": C.DEFAULT_CYCLE,
        "plen": C.DEFAULT_PERIOD,
        "onb_start": None,
        "days": [],               # отмеченные дни месячных
        "flow": {},               # день → l/m/h
        "ovul": [],               # отмеченные овуляции
        "ended": [],              # начала месячных, про которые сказали «закончились»
        "moods": {},              # день → код самочувствия
        "sent_day": None,         # за какой день уже отправлены утренние сообщения
    }


def iso(d: date) -> str:
    return d.isoformat()


def from_iso(s: str) -> date:
    return date.fromisoformat(s)


def profile(d: dict) -> C.Profile:
    return C.Profile(
        period_days={from_iso(x) for x in d["days"]},
        ovulations={from_iso(x) for x in d["ovul"]},
        ended={from_iso(x) for x in d["ended"]},
        cycle_len_default=d["clen"],
        period_len_default=d["plen"],
    )


def local_now(d: dict) -> datetime:
    return datetime.now(ZoneInfo(d["tz"]))


def local_today(d: dict) -> date:
    return local_now(d).date()


def add_day(d: dict, day: date) -> None:
    s = iso(day)
    if s not in d["days"]:
        d["days"].append(s)
        d["days"].sort()


def remove_day(d: dict, day: date) -> None:
    s = iso(day)
    if s in d["days"]:
        d["days"].remove(s)
    d["flow"].pop(s, None)
    if s in d["ended"]:
        d["ended"].remove(s)


def fmt(day: date) -> str:
    return f"{day.day} {MONTHS_GEN[day.month]}"


def fmt_range(a: date, b: date) -> str:
    if a.month == b.month:
        return f"{a.day}–{b.day} {MONTHS_GEN[b.month]}"
    return f"{fmt(a)} – {fmt(b)}"


def days_word(n: int) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


async def edit(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass  # «message is not modified» — ничего страшного


# ───────────────────────── Экраны ─────────────────────────

def today_text(d: dict) -> str:
    today = local_today(d)
    p = profile(d)
    cycles = C.build_cycles(p, today)
    info = C.day_info(p, today, today, cycles)
    lines = [f"📍 <b>Сегодня {fmt(today)}</b>"]
    if not info:
        return lines[0] + "\n\nОтметь в календаре, когда начались последние месячные."

    lines.append(f"День {info.cycle_day} цикла · {T.PHASE_NAMES[info.phase]}")
    lines.append("")
    cur = C.current_period(p, today)
    last = [c for c in cycles if c.actual][-1]
    if cur:
        lines.append(f"🔴 Идут месячные, {(today - cur[0]).days + 1}-й день")
    if info.delay:
        lines.append(f"⏳ Задержка {info.delay} {days_word(info.delay)} (ожидались {fmt(last.predicted_next)})")
    else:
        until = (last.predicted_next - today).days
        when = "сегодня" if until == 0 else f"через {until} {days_word(until)}"
        lines.append(f"⭕ Следующие месячные: ~{fmt(last.predicted_next)} ({when})")

    # Окно и овуляция: текущего цикла, если ещё не прошли, иначе следующего
    c = next((c for c in cycles if c.fertile_end >= today), None)
    if c and info.phase != C.PHASE_LATE:
        lines.append(f"🟡 Можно забеременеть: {fmt_range(c.fertile_start, c.fertile_end)} (прогноз)")
        mark = "отмечена" if c.ovulation_exact else "прогноз"
        lines.append(f"🥚 Овуляция: {'' if c.ovulation_exact else '~'}{fmt(c.ovulation)} ({mark})")

    if d["tips"]:
        lines += ["", "💡 " + T.tip_for(info.phase, today.toordinal())]
    if d["mood"]:
        lines += ["", T.MOOD_QUESTION]
    return "\n".join(lines)


def moods_of(d: dict, s: str) -> list[str]:
    """Отметки самочувствия за день (раньше хранилась одна строка — поддерживаем и её)."""
    v = d["moods"].get(s) or []
    return [v] if isinstance(v, str) else list(v)


def mood_rows(day: date, selected=()) -> list[list[InlineKeyboardButton]]:
    """Кнопки самочувствия с подписями, по 3 в ряд. Выбранные — с галочкой."""
    buttons = [
        btn(("✅ " if code in selected else e + " ") + T.MOOD_LABELS[code], f"md:{iso(day)}:{code}")
        for code, e in T.MOODS.items()
    ]
    return [buttons[:3], buttons[3:]]


def today_kb(d: dict) -> InlineKeyboardMarkup:
    today = local_today(d)
    cur = C.current_period(profile(d), today)
    rows = []
    if cur:
        rows.append([btn("✅ Месячные закончились", f"pe:{iso(cur[0])}")])
    else:
        rows.append([btn("🔴 Начались месячные", "sn:today")])
    rows.append([btn("📅 Календарь", f"cal:{today:%Y-%m}")])
    if d["mood"]:
        rows += mood_rows(today, moods_of(d, iso(today)))
    return kb(*rows)


def calendar_text() -> str:
    return f"📅 <b>Календарь</b>\n{LEGEND}\n\nНажми на день, чтобы отметить месячные или овуляцию."


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    m = year * 12 + (month - 1) + delta
    return m // 12, m % 12 + 1


def calendar_kb(d: dict, year: int, month: int) -> InlineKeyboardMarkup:
    today = local_today(d)
    p = profile(d)
    months_ahead = max(0, (year - today.year) * 12 + month - today.month)
    cycles = C.build_cycles(p, today, ahead=3 + months_ahead * 2)
    py, pm = shift_month(year, month, -1)
    ny, nm = shift_month(year, month, 1)
    rows = [[btn("◀️", f"cal:{py}-{pm:02d}"), btn(f"{MONTHS_NOM[month]} {year}", "noop"),
             btn("▶️", f"cal:{ny}-{nm:02d}")],
            [btn(w, "noop") for w in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")]]
    for week in calendar.monthcalendar(year, month):
        row = []
        for n in week:
            if n == 0:
                row.append(btn(" ", "noop"))
                continue
            day = date(year, month, n)
            mark = C.day_mark(p, day, today, cycles)
            label = MARK_EMOJI.get(mark, "📍" if day == today else "") + str(n)
            row.append(btn(label, f"day:{iso(day)}"))
        rows.append(row)
    rows.append([btn(BTN_TODAY, "today")])
    return kb(*rows)


def day_text(d: dict, day: date) -> str:
    today = local_today(d)
    p = profile(d)
    cycles = C.build_cycles(p, today, ahead=24)
    info = C.day_info(p, day, today, cycles)
    lines = [f"<b>{fmt(day)}, {WEEKDAYS[day.weekday()]}</b>"]
    if info:
        lines.append(f"День {info.cycle_day} цикла · {T.PHASE_NAMES[info.phase]}")
    s = iso(day)
    if s in d["days"]:
        flow = T.FLOWS.get(d["flow"].get(s, ""), "")
        lines.append("🔴 Отмечены месячные" + (f" · {flow}" if flow else ""))
    if info:
        c = info.cycle
        if day == c.ovulation:
            lines.append("🥚 Овуляция " + ("(отмечена)" if c.ovulation_exact else "(прогноз)"))
        elif c.in_fertile(day):
            lines.append("🟡 Можно забеременеть (прогноз)")
        if day > today and c.start <= day <= c.period_end and s not in d["days"]:
            lines.append("⭕ Прогноз месячных")
    if moods_of(d, s):
        lines.append("Самочувствие: " + ", ".join(f"{T.MOODS[c]} {T.MOOD_NAMES[c]}" for c in moods_of(d, s)))
    if day > today + timedelta(days=1):
        lines.append("\nЭто будущий день — тут только прогноз.")
    return "\n".join(lines)


def day_kb(d: dict, day: date) -> InlineKeyboardMarkup:
    today = local_today(d)
    s = iso(day)
    rows = []
    if day <= today:
        if s in d["days"]:
            rows.append([btn(("✅ " if d["flow"].get(s) == k else "") + v, f"df:{s}:{k}")
                         for k, v in T.FLOWS.items()])
            rows.append([btn("❌ Снять отметку месячных", f"dp:{s}")])
        else:
            rows.append([btn("🔴 Отметить месячные", f"dp:{s}")])
    if day <= today + timedelta(days=1):
        if s in d["ovul"]:
            rows.append([btn("❌ Снять овуляцию", f"do:{s}")])
        else:
            rows.append([btn("🥚 Это день овуляции (по тесту/УЗИ)", f"do:{s}")])
    rows.append([btn("⬅️ К календарю", f"cal:{day:%Y-%m}")])
    return kb(*rows)


def stats_text(d: dict) -> str:
    today = local_today(d)
    p = profile(d)
    st = C.compute_stats(p, today)
    lines = ["📊 <b>Твоя статистика</b>", ""]
    if st.cycle_from_data:
        n = len(st.cycle_lengths)
        lines.append(f"Средний цикл: <b>{st.cycle_len} {days_word(st.cycle_len)}</b> (по {n} "
                     f"{'циклу' if n == 1 else 'циклам'})")
        lines.append("Последние циклы: " + " · ".join(map(str, st.cycle_lengths)))
    else:
        lines.append(f"Средний цикл: <b>{st.cycle_len} {days_word(st.cycle_len)}</b> — из анкеты. "
                     "После пары циклов бот посчитает твой.")
    src = "по отметкам" if st.period_lengths else "из анкеты"
    lines.append(f"Месячные: <b>{st.period_len} {days_word(st.period_len)}</b> ({src})")
    if st.luteal_lengths:
        lines.append(f"Лютеиновая фаза: <b>{st.luteal} {days_word(st.luteal)}</b> (по отмеченным овуляциям)")
    else:
        lines.append(f"Лютеиновая фаза: {st.luteal} {days_word(st.luteal)} — стандарт для прогноза. "
                     "Отметь овуляцию по тесту, и бот посчитает твою.")

    warn = []
    if st.spread is not None:
        if st.spread <= 7:
            lines.append(f"Разброс: {st.spread} {days_word(st.spread)} — цикл стабильный")
        else:
            lines.append(f"Разброс: {st.spread} {days_word(st.spread)}")
            if st.spread > 9:
                warn.append("Циклы заметно скачут. Иногда так бывает, но если это повторяется — "
                            "стоит обсудить с гинекологом.")
    if st.cycle_from_data and not 21 <= st.cycle_len <= 35:
        warn.append("Средняя длина цикла вне обычного диапазона 21–35 дней — стоит обсудить с гинекологом.")
    if st.period_lengths and st.period_len > 7:
        warn.append("Месячные длятся дольше 7 дней — это тоже повод показаться врачу.")
    for w in warn:
        lines += ["", "⚠️ " + w]

    moods = {from_iso(k): moods_of(d, k) for k in d["moods"]}
    pat = C.mood_pattern(p, moods, today)
    if pat:
        code, n, total = pat
        lines += ["", f"📈 За 1–5 дней до месячных ты чаще всего отмечаешь {T.MOODS[code]} "
                      f"{T.MOOD_NAMES[code]} ({n} из {total} отмеченных дней)."]
    elif d["mood"]:
        lines += ["", "📈 Отмечай самочувствие — через 2–3 цикла здесь появятся твои закономерности."]
    return "\n".join(lines)


def settings_text(d: dict) -> str:
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"Время подсказок: <b>{d['time']}</b>\n"
        f"Часовой пояс: <b>{tz_label(d['tz'])}</b> (сейчас у тебя {tz_now(d['tz'])})\n"
        f"Режим: <b>{T.MODES[d['mode']]}</b>\n"
        f"Цикл по умолчанию: {d['clen']} дн. · месячные: {d['plen']} дн.\n"
        "<i>(как только накопятся отметки, бот считает по ним)</i>"
    )


def settings_kb(d: dict) -> InlineKeyboardMarkup:
    def onoff(key: str) -> str:
        return "вкл" if d[key] else "выкл"

    return kb(
        [btn("🕘 Время", "set:time"), btn("🌍 Часовой пояс", "set:tz")],
        [btn("Режим", "set:mode")],
        [btn("🔁 Длина цикла", "set:clen"), btn("🔴 Длина месячных", "set:plen")],
        [btn(f"💡 Подсказка дня: {onoff('tips')}", "tg:tips")],
        [btn(f"😊 Самочувствие: {onoff('mood')}", "tg:mood")],
        [btn(f"🟡 Уведомления про окно: {onoff('fertile')}", "tg:fertile")],
        [btn(f"🔕 Тихий режим: {onoff('discreet')}", "tg:discreet")],
        [btn("🔒 Приватность", "set:privacy"), btn("🗑 Удалить данные", "set:delete")],
    )


# ───────────────────────── Анкета ─────────────────────────

def picker_kb(year: int, month: int, today: date) -> InlineKeyboardMarkup:
    """Выбор дня начала последних месячных (не в будущем и не раньше чем 120 дней назад)."""
    earliest = today - timedelta(days=120)
    py, pm = shift_month(year, month, -1)
    ny, nm = shift_month(year, month, 1)
    rows = [[btn("◀️", f"pn:{py}-{pm:02d}"), btn(f"{MONTHS_NOM[month]} {year}", "noop"),
             btn("▶️", f"pn:{ny}-{nm:02d}")],
            [btn(w, "noop") for w in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")]]
    for week in calendar.monthcalendar(year, month):
        row = []
        for n in week:
            day = date(year, month, n) if n else None
            if not day or day > today or day < earliest:
                row.append(btn(" " if not day else "·", "noop"))
            else:
                row.append(btn(("📍" if day == today else "") + str(n), f"pk:{iso(day)}"))
        rows.append(row)
    return kb(*rows)


PLEN_KB = kb(
    [btn(str(n), f"ob:plen:{n}") for n in range(2, 6)],
    [btn(str(n), f"ob:plen:{n}") for n in range(6, 10)],
    [btn("Не знаю", f"ob:plen:{C.DEFAULT_PERIOD}")],
)

CLEN_PROMPT = (
    "🔁 <b>Какой у тебя обычно цикл?</b>\n\n"
    "Напиши число дней — от первого дня одних месячных до первого дня следующих. Например: <b>29</b>"
)
CLEN_KB = kb([btn("Не знаю", "ob:clen:x")])


def mode_kb() -> InlineKeyboardMarkup:
    return kb(*[[btn(name, f"ob:mode:{key}")] for key, name in T.MODES.items()])


MODE_PROMPT = "<b>Как будешь пользоваться ботом?</b>\n\n" + "\n".join(
    f"{T.MODES[k]} — {T.MODE_HINTS[k]}" for k in T.MODES
)

TIME_KB = kb(*[[btn(f"{h:02d}:00", f"ob:time:{h:02d}") for h in range(start, start + 6)]
               for start in (6, 12, 18)])
TIME_PROMPT = "🕘 <b>Во сколько присылать подсказку дня и напоминания?</b>"

def tz_kb() -> InlineKeyboardMarkup:
    """Кнопки с текущим временем в каждом поясе (собираются в момент показа)."""
    buttons = [btn(tz_now(tz), f"ob:tz:{tz}") for tz in TIMEZONES]
    return kb(*[buttons[i:i + 3] for i in range(0, len(buttons), 3)])


TZ_PROMPT = ("🕘 <b>Сколько у тебя сейчас времени?</b>\n\n"
             "Нажми на время, которое видишь на своих часах. Так напоминания будут приходить вовремя.")


async def send_step(target: Message, d: dict) -> None:
    """Показать текущий шаг анкеты."""
    step = d["step"]
    today = local_today(d)
    if step == "privacy":
        await target.answer(T.PRIVACY, reply_markup=kb([btn("Понятно, начинаем", "pv:ok")],
                                                       [btn("Подробнее", "pv:more")]))
    elif step == "start":
        await target.answer("📅 <b>Когда начались последние месячные?</b>\n\nВыбери первый день. "
                            "Если не помнишь точно — выбери примерно.",
                            reply_markup=picker_kb(today.year, today.month, today))
    elif step == "plen":
        await target.answer("🔴 <b>Сколько дней обычно длятся месячные?</b>", reply_markup=PLEN_KB)
    elif step == "clen":
        await target.answer(CLEN_PROMPT, reply_markup=CLEN_KB)
    elif step == "mode":
        await target.answer(MODE_PROMPT, reply_markup=mode_kb())
    elif step == "time":
        await target.answer(TIME_PROMPT, reply_markup=TIME_KB)
    elif step == "tz":
        await target.answer(TZ_PROMPT, reply_markup=tz_kb())


async def advance(cb: CallbackQuery, d: dict, step: str) -> None:
    d["step"] = step
    store.put(cb.from_user.id, d)
    await send_step(cb.message, d)


async def finish_onboarding(cb: CallbackQuery, d: dict) -> None:
    d["step"] = "done"
    d["sent_day"] = iso(local_today(d))  # сегодняшние утренние сообщения уже не нужны
    store.put(cb.from_user.id, d)
    await cb.message.answer(
        "✅ <b>Готово!</b>\n\n"
        f"Каждый день в {d['time']} бот будет присылать подсказку, а перед месячными — напоминание. "
        "Когда месячные начнутся, бот спросит — просто нажми «Да».\n\n"
        "Кнопки внизу: календарь, сегодняшний день, статистика и настройки.",
        reply_markup=MAIN_KB,
    )
    await cb.message.answer(today_text(d), reply_markup=today_kb(d))


# ───────────────────────── Команды ─────────────────────────

def get_user(user_id: int) -> dict | None:
    return store.get(user_id)


def ready(d: dict | None) -> bool:
    return bool(d) and d["step"] == "done"


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    d = get_user(message.from_user.id)
    if d is None:
        if INVITE_CODE and (command.args or "").strip() != INVITE_CODE:
            await message.answer("🔒 Этот бот приватный. Попроси ссылку-приглашение у подруги.")
            return
        d = new_user()
        store.put(message.from_user.id, d)
        await message.answer("Привет! Это <b>Period.</b> — календарь цикла: напомню о месячных, подскажу, как ты "
                             "можешь себя чувствовать в разные дни, и посчитаю овуляцию.")
        await send_step(message, d)
    elif d["step"] != "done":
        await send_step(message, d)
    else:
        await message.answer("С возвращением!", reply_markup=MAIN_KB)
        await message.answer(today_text(d), reply_markup=today_kb(d))


@router.message(Command("today"))
@router.message(F.text == BTN_TODAY)
async def cmd_today(message: Message) -> None:
    d = get_user(message.from_user.id)
    if not ready(d):
        await message.answer("Сначала заполни анкету: /start")
        return
    await message.answer(today_text(d), reply_markup=today_kb(d))


@router.message(Command("calendar"))
@router.message(F.text == BTN_CAL)
async def cmd_calendar(message: Message) -> None:
    d = get_user(message.from_user.id)
    if not ready(d):
        await message.answer("Сначала заполни анкету: /start")
        return
    today = local_today(d)
    await message.answer(calendar_text(), reply_markup=calendar_kb(d, today.year, today.month))


@router.message(Command("stats"))
@router.message(F.text == BTN_STATS)
async def cmd_stats(message: Message) -> None:
    d = get_user(message.from_user.id)
    if not ready(d):
        await message.answer("Сначала заполни анкету: /start")
        return
    await message.answer(stats_text(d))


@router.message(Command("settings"))
@router.message(F.text == BTN_SET)
async def cmd_settings(message: Message) -> None:
    d = get_user(message.from_user.id)
    if not ready(d):
        await message.answer("Сначала заполни анкету: /start")
        return
    await message.answer(settings_text(d), reply_markup=settings_kb(d))


@router.message(Command("privacy"))
async def cmd_privacy(message: Message) -> None:
    await message.answer(T.PRIVACY, reply_markup=kb([btn("Подробнее", "pv:more")]))


DELETE_TEXT = ("🗑 <b>Удалить все твои данные?</b>\n\nКалендарь, отметки и настройки сотрутся "
               "полностью и навсегда. Восстановить их будет нельзя.")
DELETE_KB = kb([btn("🗑 Да, удалить всё", "del:yes")], [btn("Отмена", "del:no")])


@router.message(Command("delete"))
async def cmd_delete(message: Message) -> None:
    if get_user(message.from_user.id) is None:
        await message.answer("У бота нет твоих данных.")
        return
    await message.answer(DELETE_TEXT, reply_markup=DELETE_KB)


@router.message(Command("invite"))
async def cmd_invite(message: Message) -> None:
    if not ready(get_user(message.from_user.id)):
        await message.answer("Сначала заполни анкету: /start")
        return
    link = f"https://t.me/{BOT_USERNAME}" + (f"?start={INVITE_CODE}" if INVITE_CODE else "")
    await message.answer(f"🔗 Ссылка для подруги:\n{link}\n\nЕё данные тоже будут видны только ей.")


@router.message(Command("count"))
async def cmd_count(message: Message) -> None:
    # Единственное, что видит создательница бота: сколько всего людей. Без имён.
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer(f"👥 Ботом пользуются: {store.count()}")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>Что умеет Period.</b>\n\n"
        "/today — сегодняшний день цикла и подсказка\n"
        "/calendar — календарь: отметить месячные или овуляцию\n"
        "/stats — средний цикл, месячные, закономерности\n"
        "/settings — время, часовой пояс, режим, тихий режим\n"
        "/invite — ссылка на бота для подруги\n"
        "/privacy — кто видит твои данные\n"
        "/delete — удалить всё\n\n"
        "Прогнозы считаются по среднему твоих циклов. Овуляция — примерно за 14 дней до следующих "
        "месячных, а если отметишь её по тесту — бот запомнит твою личную длину второй фазы.\n\n"
        "⚠️ Бот не заменяет врача и не является контрацепцией."
    )


@router.message(F.text)
async def on_text(message: Message) -> None:
    """Свободный текст нужен только для длины цикла. Содержимое никуда не записывается."""
    d = get_user(message.from_user.id)
    if d is None:
        await message.answer("Нажми /start, чтобы начать.")
        return
    waiting_clen = d["step"] == "clen" or d.get("await") == "clen"
    if not waiting_clen:
        await message.answer("Я понимаю только кнопки. Меню — внизу, справка — /help")
        return
    raw = message.text.strip().split()[0] if message.text.strip() else ""
    if not raw.isdigit() or not 15 <= int(raw) <= 60:
        await message.answer("Напиши просто число от 15 до 60, например: <b>29</b>", reply_markup=CLEN_KB)
        return
    d["clen"] = int(raw)
    note = ""
    if not 21 <= d["clen"] <= 35:
        note = ("\n\nОбычная длина цикла — от 21 до 35 дней. Если твой цикл часто выходит за эти рамки, "
                "это стоит обсудить с гинекологом.")
    if d["step"] == "clen":
        d["step"] = "mode"
        store.put(message.from_user.id, d)
        await message.answer(f"Записано: {d['clen']} {days_word(d['clen'])} ✅{note}")
        await send_step(message, d)
    else:
        d["await"] = None
        store.put(message.from_user.id, d)
        await message.answer(f"Записано: {d['clen']} {days_word(d['clen'])} ✅{note}")
        await message.answer(settings_text(d), reply_markup=settings_kb(d))


# ───────────────────────── Кнопки ─────────────────────────

@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery) -> None:
    await cb.answer()


async def user_or_alert(cb: CallbackQuery) -> dict | None:
    d = get_user(cb.from_user.id)
    if d is None:
        await cb.answer("Нажми /start, чтобы начать", show_alert=True)
    return d


@router.callback_query(F.data.startswith("pv:"))
async def cb_privacy(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if d is None:
        return
    await cb.answer()
    if cb.data == "pv:more":
        markup = kb([btn("Понятно, начинаем", "pv:ok")]) if d["step"] == "privacy" else None
        await cb.message.answer(T.PRIVACY_MORE, reply_markup=markup)
    elif d["step"] == "privacy":
        await cb.message.edit_reply_markup(reply_markup=None)
        await advance(cb, d, "start")


@router.callback_query(F.data.startswith("pn:"))
async def cb_picker_nav(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if d is None:
        return
    y, m = map(int, cb.data[3:].split("-"))
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=picker_kb(y, m, local_today(d)))
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("pk:"))
async def cb_pick(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if d is None or d["step"] != "start":
        await cb.answer()
        return
    day = from_iso(cb.data[3:])
    d["onb_start"] = iso(day)
    await cb.answer()
    await edit(cb, f"📅 Последние месячные начались <b>{fmt(day)}</b> ✅")
    await advance(cb, d, "plen")


@router.callback_query(F.data.startswith("ob:"))
async def cb_onboarding(cb: CallbackQuery) -> None:
    """Шаги анкеты. Те же кнопки используются в настройках — тогда возвращаемся в настройки."""
    d = await user_or_alert(cb)
    if d is None:
        return
    _, key, value = cb.data.split(":", 2)
    onb = d["step"] != "done"
    if onb and d["step"] != key:
        await cb.answer()  # старая кнопка от уже пройденного шага
        return
    await cb.answer()
    today = local_today(d)

    if key == "plen":
        d["plen"] = int(value)
        await edit(cb, f"🔴 Месячные длятся <b>{d['plen']} {days_word(d['plen'])}</b> ✅")
        if onb:
            start = from_iso(d["onb_start"])
            for i in range(d["plen"]):
                day = start + timedelta(days=i)
                if day <= today:
                    add_day(d, day)
            await advance(cb, d, "clen")
            return
    elif key == "clen":  # «Не знаю»
        d["clen"] = C.DEFAULT_CYCLE
        d["await"] = None
        await edit(cb, "🔁 Возьму <b>28 дней</b> — это частая средняя длина. "
                       "После пары циклов бот посчитает твою ✅")
        if onb:
            await advance(cb, d, "mode")
            return
    elif key == "mode":
        d["mode"] = value
        await edit(cb, f"Режим: <b>{T.MODES[value]}</b> ✅\n{T.MODE_HINTS[value]}")
        if value == "avoid":
            await cb.message.answer(T.DISCLAIMER_AVOID)
        if onb:
            await advance(cb, d, "time")
            return
    elif key == "time":
        d["time"] = f"{value}:00"
        # Новое время уже прошло сегодня — не отправляем сегодня повторно
        if local_now(d).strftime("%H:%M") >= d["time"]:
            d["sent_day"] = iso(today)
        await edit(cb, f"🕘 Подсказки будут приходить в <b>{d['time']}</b> ✅")
        if onb:
            await advance(cb, d, "tz")
            return
    elif key == "tz":
        if value not in TIMEZONES:
            return
        d["tz"] = value
        await edit(cb, f"🕘 Сейчас у тебя <b>{tz_now(value)}</b> — часовой пояс настроен ✅")
        if onb:
            await finish_onboarding(cb, d)
            return

    store.put(cb.from_user.id, d)
    await cb.message.answer(settings_text(d), reply_markup=settings_kb(d))


@router.callback_query(F.data == "today")
async def cb_today(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if not ready(d):
        return
    await cb.answer()
    await edit(cb, today_text(d), today_kb(d))


@router.callback_query(F.data.startswith("cal:"))
async def cb_calendar(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if not ready(d):
        return
    y, m = map(int, cb.data[4:].split("-"))
    await cb.answer()
    await edit(cb, calendar_text(), calendar_kb(d, y, m))


@router.callback_query(F.data.startswith("day:"))
async def cb_day(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if not ready(d):
        return
    day = from_iso(cb.data[4:])
    await cb.answer()
    await edit(cb, day_text(d, day), day_kb(d, day))


@router.callback_query(F.data.startswith("dp:"))
async def cb_toggle_period(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if not ready(d):
        return
    day = from_iso(cb.data[3:])
    if day > local_today(d):
        await cb.answer("Будущие дни отметить нельзя")
        return
    if iso(day) in d["days"]:
        remove_day(d, day)
        await cb.answer("Отметка снята")
    else:
        add_day(d, day)
        await cb.answer("🔴 Отмечено")
    store.put(cb.from_user.id, d)
    await edit(cb, day_text(d, day), day_kb(d, day))


@router.callback_query(F.data.startswith("df:"))
async def cb_day_flow(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if not ready(d):
        return
    _, s, flow = cb.data.split(":")
    d["flow"][s] = flow
    store.put(cb.from_user.id, d)
    await cb.answer("Записано")
    day = from_iso(s)
    await edit(cb, day_text(d, day), day_kb(d, day))


@router.callback_query(F.data.startswith("do:"))
async def cb_toggle_ovulation(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if not ready(d):
        return
    s = cb.data[3:]
    if s in d["ovul"]:
        d["ovul"].remove(s)
        await cb.answer("Отметка овуляции снята")
    else:
        d["ovul"].append(s)
        await cb.answer("🥚 Овуляция отмечена — прогнозы станут точнее")
    store.put(cb.from_user.id, d)
    day = from_iso(s)
    await edit(cb, day_text(d, day), day_kb(d, day))


def flow_kb(day: date, start: date | None) -> InlineKeyboardMarkup:
    rows = [[btn(v, f"fl:{iso(day)}:{k}") for k, v in T.FLOWS.items()]]
    if start:
        rows.append([btn("✅ Закончились", f"pe:{iso(start)}")])
    return kb(*rows)


@router.callback_query(F.data.startswith("st:"))
async def cb_started(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if not ready(d):
        return
    await cb.answer()
    today = local_today(d)
    what = cb.data[3:]
    if what == "today":
        add_day(d, today)
        store.put(cb.from_user.id, d)
        await edit(cb, "🔴 Записано: месячные начались сегодня. Новый цикл пошёл.\n\nКакие сегодня?",
                   flow_kb(today, None))
        await send_pending_tip(cb, d, today)
    elif what == "early":
        days = [today - timedelta(days=k) for k in range(10, 0, -1)]
        await edit(cb, "📅 В какой день начались?",
                   kb([btn(f"{x.day:02d}.{x.month:02d}", f"se:{iso(x)}") for x in days[:5]],
                      [btn(f"{x.day:02d}.{x.month:02d}", f"se:{iso(x)}") for x in days[5:]]))
    else:
        await edit(cb, "Хорошо, спрошу завтра.")
        await send_pending_tip(cb, d, today)


async def send_pending_tip(cb: CallbackQuery, d: dict, today: date) -> None:
    """Подсказка дня, отложенная до ответа на «Начались?», — уже с правильной фазой."""
    if d.get("tip_pending") != iso(today):
        return
    d["tip_pending"] = None
    store.put(cb.from_user.id, d)
    await send_tip(cb.bot, cb.from_user.id, d, today)


@router.callback_query(F.data.startswith("sn:"))
async def cb_started_now(cb: CallbackQuery) -> None:
    """Кнопка «Начались месячные» из подсказки дня или экрана «Сегодня».

    Исходное сообщение не стираем (там подсказка и самочувствие) — отвечаем новым.
    """
    d = await user_or_alert(cb)
    if not ready(d):
        return
    today = local_today(d)
    if iso(today) in d["days"]:
        await cb.answer("Уже отмечено")
        return
    add_day(d, today)
    store.put(cb.from_user.id, d)
    await cb.answer("🔴 Записано")

    if cb.data == "sn:today":
        await edit(cb, today_text(d), today_kb(d))
    elif cb.message.reply_markup:
        rows = [row for row in cb.message.reply_markup.inline_keyboard
                if not any((b.callback_data or "").startswith("sn:") for b in row)]
        try:
            await cb.message.edit_reply_markup(reply_markup=kb(*rows) if rows else None)
        except TelegramBadRequest:
            pass

    markup = flow_kb(today, None)
    markup.inline_keyboard.append([btn("Начались не сегодня", "st:early")])
    await cb.message.answer("🔴 Записано: месячные начались сегодня. Новый цикл пошёл.\n\nКакие сегодня?",
                            reply_markup=markup)


@router.callback_query(F.data.startswith("se:"))
async def cb_started_early(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if not ready(d):
        return
    await cb.answer()
    today = local_today(d)
    start = from_iso(cb.data[3:])
    st = C.compute_stats(profile(d), today)
    end = min(today, start + timedelta(days=st.period_len - 1))
    day = start
    while day <= end:
        add_day(d, day)
        day += timedelta(days=1)
    store.put(cb.from_user.id, d)
    text = (f"🔴 Записано: месячные с {fmt_range(start, end) if end > start else fmt(start)}.\n"
            "Если что-то не так — поправь в календаре.")
    if end == today:
        await edit(cb, text + "\n\nКакие сегодня?", flow_kb(today, start))
    else:
        d["ended"].append(iso(start))
        store.put(cb.from_user.id, d)
        await edit(cb, text)
    await send_pending_tip(cb, d, today)


@router.callback_query(F.data.startswith("fl:"))
async def cb_flow(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if not ready(d):
        return
    _, s, flow = cb.data.split(":")
    add_day(d, from_iso(s))
    d["flow"][s] = flow
    store.put(cb.from_user.id, d)
    await cb.answer("Записано")
    await edit(cb, f"🔴 {fmt(from_iso(s))}: {T.FLOWS[flow]} ✅")


@router.callback_query(F.data.startswith("pe:"))
async def cb_period_ended(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if not ready(d):
        return
    start = from_iso(cb.data[3:])
    if iso(start) not in d["ended"]:
        d["ended"].append(iso(start))
    store.put(cb.from_user.id, d)
    group = next((g for g in C.group_periods(profile(d).period_days) if g[0] == start), None)
    n = (group[1] - group[0]).days + 1 if group else 0
    await cb.answer()
    await edit(cb, f"✅ Записано: месячные длились {n} {days_word(n)}. "
                   "Если отметила не все дни — поправь в календаре.")


@router.callback_query(F.data.startswith("md:"))
async def cb_mood(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if not ready(d):
        return
    _, s, code = cb.data.split(":")
    if code not in T.MOODS:
        await cb.answer()
        return
    selected = moods_of(d, s)
    if code in selected:
        selected.remove(code)
        await cb.answer(f"Убрано: {T.MOOD_LABELS[code].lower()}")
    else:
        selected.append(code)
        await cb.answer(f"Записано: {T.MOOD_LABELS[code].lower()}. Можно выбрать ещё.")
    if selected:
        d["moods"][s] = selected
    else:
        d["moods"].pop(s, None)
    store.put(cb.from_user.id, d)
    # Ставим галочки на выбранных кнопках, остальные кнопки сообщения не трогаем
    day = from_iso(s)
    new_mood = iter([b for row in mood_rows(day, selected) for b in row])
    rows = [[next(new_mood) if (b.callback_data or "").startswith("md:") else b for b in row]
            for row in cb.message.reply_markup.inline_keyboard] if cb.message.reply_markup else []
    try:
        await cb.message.edit_reply_markup(reply_markup=kb(*rows))
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("set:"))
async def cb_settings(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if not ready(d):
        return
    await cb.answer()
    what = cb.data[4:]
    if what == "time":
        await cb.message.answer(TIME_PROMPT, reply_markup=TIME_KB)
    elif what == "tz":
        await cb.message.answer(TZ_PROMPT, reply_markup=tz_kb())
    elif what == "mode":
        await cb.message.answer(MODE_PROMPT, reply_markup=mode_kb())
    elif what == "plen":
        await cb.message.answer("🔴 <b>Сколько дней обычно длятся месячные?</b>", reply_markup=PLEN_KB)
    elif what == "clen":
        d["await"] = "clen"
        store.put(cb.from_user.id, d)
        await cb.message.answer(CLEN_PROMPT, reply_markup=CLEN_KB)
    elif what == "privacy":
        await cb.message.answer(T.PRIVACY, reply_markup=kb([btn("Подробнее", "pv:more")]))
    elif what == "delete":
        await cb.message.answer(DELETE_TEXT, reply_markup=DELETE_KB)


@router.callback_query(F.data.startswith("tg:"))
async def cb_toggle(cb: CallbackQuery) -> None:
    d = await user_or_alert(cb)
    if not ready(d):
        return
    key = cb.data[3:]
    if key not in ("tips", "mood", "fertile", "discreet"):
        await cb.answer()
        return
    d[key] = not d[key]
    store.put(cb.from_user.id, d)
    await cb.answer("Готово")
    await edit(cb, settings_text(d), settings_kb(d))


@router.callback_query(F.data.startswith("del:"))
async def cb_delete(cb: CallbackQuery) -> None:
    await cb.answer()
    if cb.data == "del:yes":
        store.delete(cb.from_user.id)
        await edit(cb, "🗑 Готово. Все твои данные удалены.")
        await cb.message.answer("Если захочешь вернуться — просто нажми /start.",
                                reply_markup=ReplyKeyboardRemove())
    else:
        await edit(cb, "Ничего не удалено.")


# ───────────────────────── Утренние сообщения ─────────────────────────

def notify(d: dict, key: str, **kw) -> str:
    text = T.NOTIFY[key][1 if d["discreet"] else 0]
    return text.format(**kw)


async def send_daily(bot: Bot, chat_id: int, d: dict, today: date) -> None:
    p = profile(d)
    events = C.daily_events(p, today)
    info_lines: list[str] = []
    fertile_on = d["fertile"] and d["mode"] in ("conceive", "avoid")

    for ev, n in events:
        if ev == C.EV_ONGOING_ASK:
            cur = C.current_period(p, today)
            await bot.send_message(chat_id, notify(d, "ongoing_ask", n=n),
                                   reply_markup=flow_kb(today, cur[0] if cur else None))
        elif ev == C.EV_START_ASK:
            text = notify(d, "start_ask_late", n=n) if n > 0 else notify(d, "start_ask")
            if n == 7:
                text += T.LATE_7
            await bot.send_message(chat_id, text, reply_markup=kb(
                [btn("Да, сегодня", "st:today"), btn("Да, раньше", "st:early")],
                [btn("Ещё нет", "st:no")]))
        elif ev == C.EV_PRE_PERIOD:
            info_lines.append(notify(d, "pre_period"))
        elif ev in (C.EV_FERTILE_SOON, C.EV_FERTILE_START, C.EV_OVULATION) and fertile_on:
            line = notify(d, ev)
            if d["mode"] == "conceive" and ev != C.EV_FERTILE_SOON:
                line += T.CONCEIVE_NOTE
            if d["mode"] == "avoid":
                line += "\nКалендарь не гарантия — нужна контрацепция."
            info_lines.append(line)

    if any(ev == C.EV_START_ASK for ev, _ in events):
        # Сначала ждём ответа «Начались?»: иначе подсказка уйдёт с фазой «перед месячными»,
        # а через минуту девушка отметит месячные. Подсказку пришлём после ответа.
        d["tip_pending"] = iso(today)
        return
    await send_tip(bot, chat_id, d, today, info_lines)


async def send_tip(bot: Bot, chat_id: int, d: dict, today: date, info_lines: list[str] | None = None) -> None:
    """Подсказка дня и вопрос о самочувствии — по актуальным отметкам."""
    info = C.day_info(profile(d), today, today)
    parts = []
    if info_lines:
        parts.append("\n\n".join(info_lines))
    if d["tips"] and info:
        header = f"📅 День {info.cycle_day}" if d["discreet"] else \
            f"📅 День {info.cycle_day} цикла · {T.PHASE_NAMES[info.phase]}"
        parts.append(f"{header}\n\n💡 {T.tip_for(info.phase, today.toordinal())}")
    if d["mood"]:
        parts.append(T.MOOD_QUESTION)
    if not parts:
        return
    rows = []
    # Месячные могут начаться не по календарю — кнопка есть в каждом дневном сообщении
    if not C.current_period(profile(d), today):
        rows.append([btn("🔴 Начались месячные", "sn:tip")])
    if d["mood"]:
        rows += mood_rows(today)
    await bot.send_message(chat_id, "\n\n".join(parts), reply_markup=kb(*rows) if rows else None)


async def tick(bot: Bot) -> None:
    for chat_id in store.chat_ids():
        try:
            d = store.get(chat_id)
            if not ready(d) or d["mode"] == "pause":
                continue
            now = local_now(d)
            today = now.date()
            if d.get("sent_day") == iso(today) or now.strftime("%H:%M") < d["time"]:
                continue
            # Сначала помечаем, потом шлём: лучше пропустить одно сообщение, чем заспамить
            d["sent_day"] = iso(today)
            store.put(chat_id, d)
            await send_daily(bot, chat_id, d, today)
            store.put(chat_id, d)  # send_daily мог отметить, что подсказка ждёт ответа
        except TelegramForbiddenError:
            pass  # девушка заблокировала бота — данные остаются, пока она не удалит их сама
        except Exception as e:  # noqa: BLE001
            # В лог пишем только тип ошибки — без данных
            logging.error("Не удалось отправить утреннее сообщение: %s", type(e).__name__)


async def scheduler(bot: Bot) -> None:
    while True:
        await tick(bot)
        await asyncio.sleep(60)


# ───────────────────────── Запуск ─────────────────────────

BOT_DESCRIPTION = (
    "Period. — календарь цикла.\n\n"
    "Напоминает о месячных, считает овуляцию и подсказывает, как ты можешь себя чувствовать "
    "в разные дни цикла.\n\n"
    "Твои данные видишь только ты."
)
BOT_SHORT_DESCRIPTION = "Календарь цикла. Твои данные видишь только ты."

async def main() -> None:
    global store, BOT_USERNAME
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN в .env")
    store = Store(DB_PATH, CRYPT_KEY)

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    BOT_USERNAME = (await bot.get_me()).username
    # Описание видно в пустом чате до /start и в профиле бота
    await bot.set_my_description(BOT_DESCRIPTION)
    await bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
    await bot.set_my_commands([
        BotCommand(command="today", description="Сегодня"),
        BotCommand(command="calendar", description="Календарь"),
        BotCommand(command="stats", description="Статистика"),
        BotCommand(command="settings", description="Настройки"),
        BotCommand(command="invite", description="Пригласить подругу"),
        BotCommand(command="privacy", description="Кто видит мои данные"),
        BotCommand(command="delete", description="Удалить мои данные"),
        BotCommand(command="help", description="Справка"),
    ])

    invite = f"https://t.me/{BOT_USERNAME}" + (f"?start={INVITE_CODE}" if INVITE_CODE else "")
    logging.info("Ссылка на бота: %s", invite)

    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
