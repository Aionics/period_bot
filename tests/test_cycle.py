import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cycle as C  # noqa: E402


def days(start: date, n: int) -> set[date]:
    return {start + timedelta(days=i) for i in range(n)}


class GroupTest(unittest.TestCase):
    def test_groups_and_gaps(self):
        d = days(date(2026, 10, 1), 5) | days(date(2026, 10, 30), 4)
        self.assertEqual(C.group_periods(d), [(date(2026, 10, 1), date(2026, 10, 5)),
                                              (date(2026, 10, 30), date(2026, 11, 2))])

    def test_missed_day_inside_period_still_one_group(self):
        d = {date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 4)}
        self.assertEqual(len(C.group_periods(d)), 1)


class PredictionTest(unittest.TestCase):
    def test_defaults_only(self):
        start = date(2026, 11, 1)
        p = C.Profile(days(start, 5), cycle_len_default=28, period_len_default=5)
        today = date(2026, 11, 10)
        cycles = C.build_cycles(p, today)
        c = cycles[0]
        self.assertEqual(c.predicted_next, date(2026, 11, 29))
        self.assertEqual(c.ovulation, date(2026, 11, 15))           # 29.11 − 14
        self.assertEqual(c.fertile_start, date(2026, 11, 10))
        self.assertEqual(c.fertile_end, date(2026, 11, 16))
        info = C.day_info(p, today, today, cycles)
        self.assertEqual(info.cycle_day, 10)
        self.assertEqual(info.phase, C.PHASE_FOLLICULAR)

    def test_average_from_real_cycles(self):
        starts = [date(2026, 7, 1), date(2026, 7, 31), date(2026, 8, 30), date(2026, 9, 29)]  # по 30 дней
        p = C.Profile(set().union(*(days(s, 4) for s in starts)), cycle_len_default=26)
        st = C.compute_stats(p, date(2026, 10, 5))
        self.assertEqual(st.cycle_len, 30)
        self.assertEqual(st.period_len, 4)
        self.assertEqual(st.spread, 0)

    def test_exact_ovulation_teaches_luteal_length(self):
        s1, s2 = date(2026, 9, 1), date(2026, 9, 29)
        p = C.Profile(days(s1, 5) | days(s2, 5), ovulations={date(2026, 9, 17)})  # 12 дней до месячных
        today = date(2026, 10, 5)
        st = C.compute_stats(p, today)
        self.assertEqual(st.luteal, 12)
        cur = [c for c in C.build_cycles(p, today) if c.start == s2][0]
        self.assertEqual(cur.ovulation, cur.predicted_next - timedelta(days=12))
        self.assertFalse(cur.ovulation_exact)

    def test_outlier_cycle_ignored(self):
        p = C.Profile(days(date(2026, 1, 1), 4) | days(date(2026, 6, 1), 4), cycle_len_default=29)
        self.assertEqual(C.compute_stats(p, date(2026, 6, 10)).cycle_len, 29)

    def test_short_cycle_ovulation_not_before_day_6(self):
        p = C.Profile(days(date(2026, 11, 1), 5), cycle_len_default=18)
        c = C.build_cycles(p, date(2026, 11, 2))[0]
        self.assertGreaterEqual(c.ovulation, date(2026, 11, 6))


class DelayTest(unittest.TestCase):
    def setUp(self):
        self.p = C.Profile(days(date(2026, 10, 1), 5), cycle_len_default=28)  # ждём 29.10

    def test_late(self):
        today = date(2026, 11, 2)
        info = C.day_info(self.p, today, today)
        self.assertEqual(info.phase, C.PHASE_LATE)
        self.assertEqual(info.delay, 4)
        expected_day = C.day_info(self.p, date(2026, 10, 29), date(2026, 10, 29))
        self.assertEqual(expected_day.phase, C.PHASE_LUTEAL_LATE)
        self.assertEqual(expected_day.delay, 0)
        self.assertIn((C.EV_START_ASK, 4), C.daily_events(self.p, today))

    def test_ask_on_expected_day(self):
        self.assertIn((C.EV_START_ASK, 0), C.daily_events(self.p, date(2026, 10, 29)))

    def test_pre_period_warning(self):
        self.assertIn((C.EV_PRE_PERIOD, 2), C.daily_events(self.p, date(2026, 10, 27)))

    def test_projection_moves_with_delay(self):
        today = date(2026, 11, 2)
        cycles = C.build_cycles(self.p, today)
        self.assertEqual(cycles[1].start, today + timedelta(days=1))


class OngoingTest(unittest.TestCase):
    def test_ongoing_ask_and_end(self):
        start = date(2026, 11, 18)
        p = C.Profile(days(start, 3))  # 18, 19, 20
        today = date(2026, 11, 21)
        self.assertIn((C.EV_ONGOING_ASK, 4), C.daily_events(p, today))
        p.ended.add(start)
        self.assertNotIn(C.EV_ONGOING_ASK, [e for e, _ in C.daily_events(p, today)])

    def test_stops_asking_after_silence(self):
        p = C.Profile(days(date(2026, 11, 15), 2))  # последний отмеченный — 16-е
        self.assertIsNone(C.current_period(p, date(2026, 11, 21)))

    def test_predicted_rest_of_period_on_calendar(self):
        today = date(2026, 11, 21)
        p = C.Profile({today}, period_len_default=5)
        cycles = C.build_cycles(p, today)
        self.assertEqual(C.day_mark(p, today + timedelta(days=2), today, cycles), C.MARK_PERIOD_PRED)
        self.assertEqual(C.day_mark(p, today, today, cycles), C.MARK_PERIOD)


class FertileEventsTest(unittest.TestCase):
    def test_fertile_events(self):
        p = C.Profile(days(date(2026, 11, 1), 5), cycle_len_default=28)  # овуляция 15.11, окно с 10.11
        ev = lambda d: [e for e, _ in C.daily_events(p, d)]  # noqa: E731
        self.assertIn(C.EV_FERTILE_SOON, ev(date(2026, 11, 8)))
        self.assertIn(C.EV_FERTILE_START, ev(date(2026, 11, 10)))
        self.assertIn(C.EV_OVULATION, ev(date(2026, 11, 15)))
        self.assertEqual(ev(date(2026, 11, 12)), [])


class MoodPatternTest(unittest.TestCase):
    def test_pattern(self):
        starts = [date(2026, 8, 1), date(2026, 8, 29), date(2026, 9, 26)]
        p = C.Profile(set().union(*(days(s, 4) for s in starts)))
        moods = {date(2026, 8, 27): "pain", date(2026, 8, 28): "pain",
                 date(2026, 9, 25): "pain", date(2026, 9, 24): "good"}
        self.assertEqual(C.mood_pattern(p, moods, date(2026, 10, 1)), ("pain", 3, 4))

    def test_several_moods_per_day(self):
        starts = [date(2026, 8, 1), date(2026, 8, 29), date(2026, 9, 26)]
        p = C.Profile(set().union(*(days(s, 4) for s in starts)))
        moods = {date(2026, 8, 27): ["tired", "pain"], date(2026, 8, 28): ["pain"],
                 date(2026, 9, 25): ["pain", "low"]}
        self.assertEqual(C.mood_pattern(p, moods, date(2026, 10, 1)), ("pain", 3, 3))


class StorageTest(unittest.TestCase):
    def test_encrypted_and_deleted(self):
        from cryptography.fernet import Fernet
        from storage import Store

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.db")
            s = Store(path, Fernet.generate_key().decode())
            s.put(42, {"days": ["2026-11-01"], "secret": "очень личное"})
            self.assertEqual(s.get(42)["days"], ["2026-11-01"])
            with open(path, "rb") as f:
                raw = f.read()
            self.assertNotIn(b"2026-11-01", raw)
            self.assertNotIn("личное".encode(), raw)
            s.delete(42)
            self.assertIsNone(s.get(42))
            self.assertEqual(s.count(), 0)


class BotTextsSmokeTest(unittest.TestCase):
    """Экраны собираются без ошибок на разных сценариях."""

    def test_screens(self):
        os.environ.setdefault("BOT_TOKEN", "1:x")
        import bot as B

        d = B.new_user()
        d.update(step="done", days=[x.isoformat() for x in days(date(2026, 10, 20), 5)])
        for screen in (B.today_text, B.stats_text, B.settings_text):
            self.assertTrue(screen(d))
        today = B.local_today(d)
        B.today_kb(d)
        B.calendar_kb(d, today.year, today.month)
        B.calendar_kb(d, today.year + 1, today.month)
        for k in range(-40, 40, 3):
            day = today + timedelta(days=k)
            B.day_text(d, day)
            B.day_kb(d, day)
        for tz in B.TIMEZONES:
            self.assertLessEqual(len(f"ob:tz:{tz}".encode()), 64)

    def test_mood_multiselect_and_old_format(self):
        os.environ.setdefault("BOT_TOKEN", "1:x")
        import bot as B

        d = B.new_user()
        d["moods"] = {"2026-11-20": "pain", "2026-11-21": ["pain", "tired"]}
        self.assertEqual(B.moods_of(d, "2026-11-20"), ["pain"])
        labels = [b.text for r in B.mood_rows(date(2026, 11, 21), B.moods_of(d, "2026-11-21")) for b in r]
        self.assertEqual(sum(t.startswith("✅") for t in labels), 2)


class DailyMessagesTest(unittest.TestCase):
    """Утренние сообщения на поддельном боте."""

    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, reply_markup=None):
            self.sent.append(text)

    def test_tip_waits_for_start_answer(self):
        import asyncio
        os.environ.setdefault("BOT_TOKEN", "1:x")
        import bot as B

        d = B.new_user()
        d.update(step="done", days=[x.isoformat() for x in days(date(2026, 10, 27), 4)])  # ждём 24.11
        bot = self.FakeBot()
        asyncio.run(B.send_daily(bot, 1, d, date(2026, 11, 24)))
        self.assertEqual(len(bot.sent), 1)                 # только «Начались?»
        self.assertIn("начались", bot.sent[0])
        self.assertEqual(d["tip_pending"], "2026-11-24")

        # Ответила «Да, сегодня» — подсказка уже про месячные, а не «перед месячными»
        B.add_day(d, date(2026, 11, 24))
        asyncio.run(B.send_tip(bot, 1, d, date(2026, 11, 24)))
        self.assertIn("День 1 цикла", bot.sent[1])
        self.assertIn("месячные", bot.sent[1].split("\n")[0])

    def test_tip_sent_right_away_on_normal_day(self):
        import asyncio
        os.environ.setdefault("BOT_TOKEN", "1:x")
        import bot as B

        d = B.new_user()
        d.update(step="done", days=[x.isoformat() for x in days(date(2026, 11, 1), 4)])
        bot = self.FakeBot()
        asyncio.run(B.send_daily(bot, 1, d, date(2026, 11, 12)))
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("День 12 цикла", bot.sent[0])
        self.assertIsNone(d.get("tip_pending"))


if __name__ == "__main__":
    unittest.main()
