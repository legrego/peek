import datetime
import sqlite3
from os.path import expanduser
from typing import Iterable, List

from prompt_toolkit.history import History

from peek.config import config_location, ensure_dir_exists

HIST_MAX = 10_000


class SqLiteHistory(History):

    def __init__(self, history_max=HIST_MAX):
        super().__init__()
        self.history_max = history_max
        db_file = expanduser(config_location() + 'history')
        ensure_dir_exists(db_file)
        self.conn = sqlite3.connect(db_file)
        self.conn.execute('CREATE TABLE IF NOT EXISTS history '
                          '(id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, timestamp INTEGER NOT NULL)')
        self._maintain_size()
        self.conn.commit()

    def __del__(self):
        self.conn.close()

    def _maintain_size(self):
        c = self.conn.cursor()
        res = c.execute('SELECT COUNT(*) from history').fetchone()
        if res is None or res[0] < self.history_max:
            return
        c.execute('DELETE FROM history where id in (SELECT id FROM history ORDER BY id limit ?)',
                  (res[0] - self.history_max,))

    def load_history_strings(self) -> Iterable[str]:
        strings: List[str] = []
        for row in self.conn.execute('SELECT * FROM history ORDER BY id DESC'):
            strings.append(row[1])
        return strings

    def store_string(self, string: str) -> None:
        self.conn.execute("INSERT INTO history(content, timestamp) VALUES (?, ?)", (string, datetime.datetime.now()))
        self.conn.commit()
