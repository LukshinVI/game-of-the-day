"""ops/backup/offsite-git.sh against a local bare repository: only ciphertext is pushed,
the newest KEEP copies stay in the tip, verify/restore decrypt and check, restore never
overwrites. Also: no database or backup artifact is ever tracked in this repository.
"""

import gzip
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "backup" / "offsite-git.sh"


@unittest.skipUnless(all(shutil.which(t) for t in ("age", "age-keygen", "git", "sqlite3")),
                     "age, git and the sqlite3 CLI are required")
class OffsiteGit(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "deploy_key").write_text("unused for a local remote\n")
        subprocess.run(["age-keygen", "-o", str(self.state / "age.key")], check=True, capture_output=True)
        self.remote = self.root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        db = self.root / "src.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "src/db/schema.sql").read_text())
        con.execute("INSERT INTO games(legacy_id, game_date, title) VALUES ('1', '2026-11-08', 'СекретноеНазвание')")
        con.commit()
        con.close()
        self.backups = []
        for stamp in ("20261108T000000Z", "20261109T000000Z", "20261110T000000Z"):
            path = self.root / f"game-{stamp}.db.gz"
            path.write_bytes(gzip.compress(db.read_bytes()))
            self.backups.append(path)

    def sh(self, *args, **extra):
        env = {**extra, 
            "PATH": os.environ["PATH"], "HOME": str(self.root), "TMPDIR": str(self.root),
            "GAME_OFFSITE_REPO": f"file://{self.remote}", "GAME_OFFSITE_DIR": str(self.state),
            "GAME_OFFSITE_KEEP": "2",
        }
        return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True)

    def git(self, *args):
        return subprocess.run(["git", f"--git-dir={self.remote}", *args],
                              check=True, capture_output=True).stdout

    def test_push_keeps_the_newest_copies_and_only_ciphertext(self):
        for path in self.backups:
            result = self.sh(str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
        tip = self.git("ls-tree", "-r", "--name-only", "db-backups").decode().split()
        self.assertEqual(tip, ["README", "backups/game-20261109T000000Z.db.gz.age",
                               "backups/game-20261110T000000Z.db.gz.age"])
        blob = self.git("show", "db-backups:backups/game-20261110T000000Z.db.gz.age")
        self.assertTrue(blob.startswith(b"age-encryption.org/v1"))
        for rev in self.git("rev-list", "--all").decode().split():
            for name in self.git("ls-tree", "-r", "--name-only", rev).decode().split():
                content = self.git("show", f"{rev}:{name}")
                self.assertNotIn("СекретноеНазвание".encode(), content)
                self.assertNotIn(b"SQLite format", content)

    def test_push_latest_follows_the_local_manifest(self):
        local = self.root / "local"
        local.mkdir()
        latest = self.backups[1]
        shutil.copy(latest, local / latest.name)
        (local / ".last-local-ok").write_text(f"1700000000 {latest.name}\n")
        result = self.sh("push-latest", GAME_BACKUP_DIR=str(local))
        self.assertEqual(result.returncode, 0, result.stderr)
        tip = self.git("ls-tree", "-r", "--name-only", "db-backups").decode().split()
        self.assertIn(f"backups/{latest.name}.age", tip)
        self.assertNotEqual(self.sh("push-latest", GAME_BACKUP_DIR=str(self.root / "none")).returncode, 0)

    def test_verify_and_restore(self):
        self.sh(str(self.backups[0]))
        verify = self.sh("verify")
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertIn("integrity ok, 1 games", verify.stderr)
        target = self.root / "restored.db"
        self.assertEqual(self.sh("restore", str(target)).returncode, 0)
        con = sqlite3.connect(target)
        self.assertEqual(con.execute("SELECT title FROM games").fetchall(), [("СекретноеНазвание",)])
        con.close()
        again = self.sh("restore", str(target))
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("nothing was overwritten", again.stderr)

    def test_without_the_identity_nothing_is_decrypted(self):
        self.sh(str(self.backups[0]))
        (self.state / "age.key").unlink()
        result = self.sh("verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identity missing", result.stderr)

    def test_a_corrupt_backup_is_not_pushed(self):
        bad = self.root / "game-20261111T000000Z.db.gz"
        bad.write_bytes(b"not gzip")
        self.assertNotEqual(self.sh(str(bad)).returncode, 0)
        self.assertEqual(subprocess.run(["git", f"--git-dir={self.remote}", "rev-parse", "--verify", "-q", "db-backups"],
                                        capture_output=True).returncode, 1)


class NoDataInTheRepository(unittest.TestCase):
    def test_no_database_or_backup_artifact_is_tracked(self):
        if not (ROOT / ".git").exists():
            self.skipTest("not a git checkout")
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True,
                                 check=True).stdout.split()
        bad = [f for f in tracked if f.endswith((".db", ".sqlite", ".sqlite3", ".db.gz", ".age", "-wal", "-shm"))
               or f.startswith("data/")]
        self.assertEqual(bad, [])
