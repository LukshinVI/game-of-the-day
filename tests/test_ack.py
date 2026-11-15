import sqlite3
import unittest

from src.feedback import reactions


class InstantAck(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("CREATE TABLE feedback (id INTEGER PRIMARY KEY, game_id INTEGER, raw_text TEXT, "
                         "insights_at TEXT)")

    def add(self, game, text, analysed=False):
        return self.con.execute("INSERT INTO feedback (game_id, raw_text, insights_at) VALUES (?, ?, ?)",
                                (game, text, "2026-11-15" if analysed else None)).lastrowid

    def test_the_first_message_of_a_burst_is_acknowledged(self):
        first = self.add(1, "понравилось")
        self.assertEqual(reactions.ack(self.con, 1, first, "понравилось"), reactions.ACK)
        second = self.add(1, "и ещё")
        self.assertIsNone(reactions.ack(self.con, 1, second, "и ещё"))  # one per burst

    def test_after_the_analysis_a_new_message_is_acknowledged_again(self):
        self.add(1, "было", analysed=True)
        self.add(2, "другая игра")  # another game's burst does not count
        new = self.add(1, "ещё")
        self.assertEqual(reactions.ack(self.con, 1, new, "ещё"), reactions.ACK)

    def test_voice_and_reactions(self):
        voice = self.add(1, "[голосовое] всё понравилось")
        self.assertEqual(reactions.ack(self.con, 1, voice, "[голосовое] всё понравилось"), reactions.ACK_VOICE)
        sticker = self.add(2, "[стикер 👍]")
        self.assertIsNone(reactions.ack(self.con, 2, sticker, "[стикер 👍]"))  # the nudge covers it


if __name__ == "__main__":
    unittest.main()
