"""
Расчёт цикла: месячные, овуляция, окно, когда можно забеременеть, фазы.

Опирается на исследования:
  • Bull et al., 2019 (npj Digital Medicine, 612 тыс. циклов): средний цикл 29,3 дня,
    лютеиновая фаза в среднем 12,4 дня (от 7 до 17). Поэтому овуляцию считаем
    НАЗАД от следующих месячных, а не вперёд от начала цикла.
  • Wilcox, Dunson, Baird, 2000 (BMJ): окно, когда можно забеременеть, — 6 дней,
    заканчивающихся днём овуляции. Добавляем +1 день на неточность прогноза.

Здесь нет ничего про Telegram — только даты, чтобы логику было легко проверить тестами.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean

DEFAULT_CYCLE = 28
DEFAULT_PERIOD = 5
DEFAULT_LUTEAL = 14       # стандарт для прогноза, пока нет отмеченных овуляций
NEW_PERIOD_GAP = 5        # отметки дальше чем через 5 дней друг от друга — уже новые месячные
MIN_CYCLE, MAX_CYCLE = 15, 60      # что считаем правдоподобной длиной цикла
MIN_LUTEAL, MAX_LUTEAL = 7, 18
MAX_PERIOD = 12
HISTORY = 6               # средние считаем по последним 6 циклам
FERTILE_BEFORE, FERTILE_AFTER = 5, 1
ONGOING_MAX_DAYS = 10     # дольше 10 дней бот не спрашивает «ещё идут?»

PHASE_MENSTRUAL = "menstrual"
PHASE_FOLLICULAR = "follicular"
PHASE_OVULATORY = "ovulatory"
PHASE_LUTEAL_EARLY = "luteal_early"
PHASE_LUTEAL_LATE = "luteal_late"
PHASE_LATE = "late"       # задержка


@dataclass
class Profile:
    period_days: set[date]
    ovulations: set[date] = field(default_factory=set)   # отмеченные вручную (тест, температура, УЗИ)
    ended: set[date] = field(default_factory=set)        # старты месячных, про которые сказали «закончились»
    cycle_len_default: int = DEFAULT_CYCLE
    period_len_default: int = DEFAULT_PERIOD


@dataclass
class Stats:
    starts: list[date]
    cycle_len: int
    period_len: int
    luteal: int
    cycle_lengths: list[int]       # фактические длины последних циклов
    period_lengths: list[int]
    luteal_lengths: list[int]

    @property
    def cycle_from_data(self) -> bool:
        return bool(self.cycle_lengths)

    @property
    def spread(self) -> int | None:
        if len(self.cycle_lengths) < 2:
            return None
        return max(self.cycle_lengths) - min(self.cycle_lengths)


@dataclass
class Cycle:
    start: date
    next_start: date          # фактическое начало следующего цикла или прогноз
    predicted_next: date      # прогноз начала следующих месячных (не двигается при задержке)
    actual: bool              # цикл начался по отметкам (а не прогноз)
    ovulation: date
    ovulation_exact: bool
    period_end: date

    @property
    def fertile_start(self) -> date:
        return self.ovulation - timedelta(days=FERTILE_BEFORE)

    @property
    def fertile_end(self) -> date:
        return self.ovulation + timedelta(days=FERTILE_AFTER)

    def in_fertile(self, d: date) -> bool:
        return self.fertile_start <= d <= self.fertile_end


@dataclass
class DayInfo:
    day: date
    cycle: Cycle
    cycle_day: int
    phase: str
    delay: int                # дней задержки (0 — задержки нет)


def group_periods(days) -> list[tuple[date, date]]:
    """Отмеченные дни → список месячных (начало, конец)."""
    groups: list[tuple[date, date]] = []
    for d in sorted(set(days)):
        if groups and (d - groups[-1][1]).days <= NEW_PERIOD_GAP:
            groups[-1] = (groups[-1][0], d)
        else:
            groups.append((d, d))
    return groups


def _is_ongoing(group: tuple[date, date], p: Profile, today: date) -> bool:
    start, end = group
    return (
        start not in p.ended
        and (today - end).days <= 1
        and (today - start).days < ONGOING_MAX_DAYS
    )


def compute_stats(p: Profile, today: date) -> Stats:
    groups = group_periods(p.period_days)
    starts = [g[0] for g in groups]

    cycle_lengths = [
        (b - a).days for a, b in zip(starts, starts[1:]) if MIN_CYCLE <= (b - a).days <= MAX_CYCLE
    ][-HISTORY:]
    cycle_len = round(mean(cycle_lengths)) if cycle_lengths else p.cycle_len_default

    finished = [g for g in groups if not _is_ongoing(g, p, today)]
    period_lengths = [
        (e - s).days + 1 for s, e in finished if 1 <= (e - s).days + 1 <= MAX_PERIOD
    ][-HISTORY:]
    period_len = round(mean(period_lengths)) if period_lengths else p.period_len_default

    luteal_lengths = []
    for o in sorted(p.ovulations):
        nxt = next((s for s in starts if s > o), None)
        if nxt and MIN_LUTEAL <= (nxt - o).days <= MAX_LUTEAL:
            luteal_lengths.append((nxt - o).days)
    luteal_lengths = luteal_lengths[-HISTORY:]
    luteal = round(mean(luteal_lengths)) if luteal_lengths else DEFAULT_LUTEAL
    # В коротком цикле лютеиновая фаза не может занимать почти весь цикл
    luteal = min(luteal, max(cycle_len - 7, MIN_LUTEAL))

    return Stats(starts, cycle_len, period_len, luteal, cycle_lengths, period_lengths, luteal_lengths)


def _ovulation_for(start: date, next_start: date, p: Profile, luteal: int) -> tuple[date, bool]:
    exact = sorted(o for o in p.ovulations if start <= o < next_start)
    if exact:
        return exact[-1], True
    ov = next_start - timedelta(days=luteal)
    earliest = start + timedelta(days=5)
    return max(ov, earliest), False


def build_cycles(p: Profile, today: date, ahead: int = 3) -> list[Cycle]:
    """Все фактические циклы + `ahead` прогнозируемых вперёд."""
    st = compute_stats(p, today)
    groups = group_periods(p.period_days)
    if not groups:
        return []
    cycles: list[Cycle] = []
    step = timedelta(days=st.cycle_len)
    plen = timedelta(days=st.period_len - 1)

    for i, (start, end) in enumerate(groups):
        if i + 1 < len(groups):
            nxt = groups[i + 1][0]
            predicted = nxt
        else:
            predicted = start + step
            # Задержка: прогноз уже прошёл, а новых отметок нет — цикл тянется до сегодня
            nxt = max(predicted, today + timedelta(days=1))
        ov, exact = _ovulation_for(start, predicted, p, st.luteal)
        if _is_ongoing((start, end), p, today):
            period_end = max(end, start + plen)
        else:
            period_end = end
        cycles.append(Cycle(start, nxt, predicted, True, ov, exact, period_end))

    for _ in range(ahead):
        start = cycles[-1].next_start
        nxt = start + step
        ov, exact = _ovulation_for(start, nxt, p, st.luteal)
        cycles.append(Cycle(start, nxt, nxt, False, ov, exact, start + plen))
    return cycles


def _phase(c: Cycle, d: date) -> str:
    if d <= c.period_end:
        return PHASE_MENSTRUAL
    if c.actual and d > c.predicted_next:
        return PHASE_LATE
    if d < c.ovulation - timedelta(days=2):
        return PHASE_FOLLICULAR
    if d <= c.ovulation + timedelta(days=1):
        return PHASE_OVULATORY
    if d < c.predicted_next - timedelta(days=5):
        return PHASE_LUTEAL_EARLY
    return PHASE_LUTEAL_LATE


def day_info(p: Profile, d: date, today: date, cycles: list[Cycle] | None = None) -> DayInfo | None:
    cycles = cycles if cycles is not None else build_cycles(p, today)
    for c in cycles:
        if c.start <= d < c.next_start:
            delay = (d - c.predicted_next).days if (c.actual and d > c.predicted_next) else 0
            return DayInfo(d, c, (d - c.start).days + 1, _phase(c, d), delay)
    return None


def current_period(p: Profile, today: date) -> tuple[date, date] | None:
    """Месячные, которые идут сейчас (начало, последний отмеченный день), или None."""
    groups = group_periods(p.period_days)
    if groups and _is_ongoing(groups[-1], p, today):
        return groups[-1]
    return None


# ───────────── Отметки на календаре ─────────────

MARK_PERIOD = "period"         # отмеченные месячные
MARK_PERIOD_PRED = "period_pred"
MARK_OVULATION = "ovulation"
MARK_FERTILE = "fertile"


def day_mark(p: Profile, d: date, today: date, cycles: list[Cycle]) -> str | None:
    if d in p.period_days:
        return MARK_PERIOD
    for c in cycles:
        if not (c.start <= d < c.next_start):
            continue
        if d > today and d <= c.period_end and d >= c.start:
            return MARK_PERIOD_PRED
        if d == c.ovulation:
            return MARK_OVULATION
        if c.in_fertile(d):
            return MARK_FERTILE
        return None
    return None


# ───────────── Что сказать сегодня ─────────────

EV_ONGOING_ASK = "ongoing_ask"     # «Ещё идут?»
EV_PRE_PERIOD = "pre_period"       # через 2 дня
EV_START_ASK = "start_ask"         # «Начались?»
EV_FERTILE_SOON = "fertile_soon"   # через 2 дня окно
EV_FERTILE_START = "fertile_start"
EV_OVULATION = "ovulation_day"


def daily_events(p: Profile, today: date) -> list[tuple[str, int]]:
    """События на сегодня: [(тип, число)], число — например, дни задержки."""
    cycles = build_cycles(p, today)
    if not cycles:
        return []
    events: list[tuple[str, int]] = []

    cur = current_period(p, today)
    if cur and cur[1] < today:
        events.append((EV_ONGOING_ASK, (today - cur[0]).days + 1))

    last_actual = [c for c in cycles if c.actual][-1]
    if not cur:
        until = (last_actual.predicted_next - today).days
        if until == 2:
            events.append((EV_PRE_PERIOD, 2))
        elif until <= 0:
            delay = -until
            if delay <= 14 or delay % 3 == 0:
                events.append((EV_START_ASK, delay))

    info = day_info(p, today, today, cycles)
    if info and info.phase != PHASE_LATE:
        c = info.cycle
        if today == c.fertile_start - timedelta(days=2):
            events.append((EV_FERTILE_SOON, 2))
        elif today == c.fertile_start:
            events.append((EV_FERTILE_START, 0))
        elif today == c.ovulation:
            events.append((EV_OVULATION, 0))
    return events


# ───────────── Личные закономерности по самочувствию ─────────────

def mood_pattern(p: Profile, moods: dict[date, list[str]], today: date,
                 skip=("good",)) -> tuple[str, int, int] | None:
    """Что она чаще всего отмечает за 1–5 дней до месячных: (код, в скольких днях, из скольких
    отмеченных дней). В один день можно отметить несколько вариантов.

    Показываем, только если набралось хотя бы 2 законченных цикла и признак встречался ≥3 раз
    в разных циклах.
    """
    starts = compute_stats(p, today).starts
    if len(starts) < 3:
        return None
    hits: list[tuple[str, date]] = []
    total = 0
    for s in starts[1:]:
        for k in range(1, 6):
            d = s - timedelta(days=k)
            codes = moods.get(d) or []
            if isinstance(codes, str):  # старый формат: один вариант на день
                codes = [codes]
            if codes:
                total += 1
                hits += [(c, s) for c in codes if c not in skip]
    if not hits:
        return None
    code, n = Counter(c for c, _ in hits).most_common(1)[0]
    cycles_with = {s for c, s in hits if c == code}
    if n >= 3 and len(cycles_with) >= 2:
        return code, n, total
    return None
