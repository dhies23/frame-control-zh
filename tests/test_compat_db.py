"""Compatibility reports without the maintainer's key: saved locally, never sent.

Run: python3 -m unittest discover -s tests
"""
import sandbox  # noqa: F401  (first: keeps tests off real data and services)
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ui"))

import frame_compat_db as db  # noqa: E402


class NoKey(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = tmp.name
        for name, value in (("STATE", state), ("OUTBOX", os.path.join(state, "outbox.jsonl")),
                            ("MIRROR", os.path.join(state, "mirror.json")),
                            ("SYNC_STATE", os.path.join(state, "posthog-sync.json"))):
            p = mock.patch.object(db, name, value)
            p.start()
            self.addCleanup(p.stop)
        db._mem.update(at=0, reports=None, source=None)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("FRAME_CONTROL_KEY", None)
        # No Keychain entry, and any network use fails the test.
        no_key = mock.patch.object(db.subprocess, "run",
                                   return_value=mock.Mock(returncode=44, stdout=""))
        no_key.start()
        self.addCleanup(no_key.stop)
        net = mock.patch.object(db._opener, "open", side_effect=AssertionError("network used"))
        net.start()
        self.addCleanup(net.stop)

    def test_report_is_kept_locally(self):
        self.assertFalse(db.shared())
        r = db.add({"package": "org.example.app", "date": "2026-09-26T10:00:00", "rating": "works"})
        reports = db.load()
        self.assertEqual([x["id"] for x in reports], [r["id"]])
        self.assertEqual(db._mem["source"], "mirror")

    def test_corrupt_mirror_non_list_reports_does_not_crash(self):
        for payload in ('{"reports": null}', '{"reports": 42}', '["not-a-dict"]'):
            with open(db.MIRROR, "w", encoding="utf-8") as f:
                f.write(payload)
            self.assertEqual(db._read_mirror(), [])
            db._mem.update(at=0, reports=None, source=None)
            self.assertEqual(db.load(), [])

    def test_outbox_invalid_utf8_bytes_quarantined_and_valid_kept(self):
        good = {"id": "ok-1", "package": "org.example.app", "date": "2026-09-26T10:00:00",
                "rating": "works", "notes": "Works — 日本語"}
        with open(db.OUTBOX, "wb") as f:
            f.write((json.dumps(good, ensure_ascii=False) + "\n").encode("utf-8"))
            f.write(b"\xff\xfe corrupt-line\n")
        rows = db._outbox()
        self.assertEqual([r["id"] for r in rows], ["ok-1"])
        self.assertEqual(rows[0]["notes"], "Works — 日本語")
        with open(db.OUTBOX + ".rejected", encoding="utf-8") as f:
            rejected = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["why"], "unreadable JSON")

    def test_outbox_serializes_with_lock(self):
        db.add({"package": "org.example.app", "date": "2026-09-26T10:00:00", "rating": "works"})
        entered = threading.Event()
        finished = threading.Event()

        def reader():
            entered.set()
            db._outbox()
            finished.set()

        with db._lock:
            # Re-entrant for callers such as flush() that already hold _lock.
            self.assertEqual(len(db._outbox()), 1)
            t = threading.Thread(target=reader)
            t.start()
            self.assertTrue(entered.wait(2))
            self.assertFalse(finished.wait(0.1))
        self.assertTrue(finished.wait(2))
        t.join(2)

    def test_malformed_server_responses_raise_db_error_and_fallback_to_mirror(self):
        mirror_row = {"id": "m-1", "package": "org.example.cached", "date": "2026-09-26T10:00:00"}
        with open(db.MIRROR, "w", encoding="utf-8") as f:
            json.dump({"reports": [mirror_row]}, f)
        os.environ["FRAME_CONTROL_KEY"] = "secret"
        for bad_page in (None, [], {"reports": None}, {"reports": [], "next": 123}):
            with mock.patch.object(db, "_request", return_value=bad_page):
                with self.assertRaises(db.DBError):
                    db.fetch_all()
                db._mem.update(at=0, reports=None, source=None)
                loaded = db.load()
                self.assertEqual([r["id"] for r in loaded], ["m-1"])
                self.assertEqual(db._mem["source"], "mirror")

        # Queued report stays in outbox if flush receives a malformed response.
        with mock.patch.object(db, "_request", side_effect=db.DBError("offline")):
            queued = db.add({"package": "org.example.app", "date": "2026-09-26T10:00:00", "rating": "works"})
        for bad_res in (None, [], {"rejected": 123}):
            with mock.patch.object(db, "_request", return_value=bad_res):
                with self.assertRaises(db.DBError):
                    db.flush()
            self.assertEqual([r["id"] for r in db._outbox()], [queued["id"]])

    def test_save_sync_state_handles_corrupt_fields_and_community_rows_rejects_bool(self):
        db._save_sync_state({"seen": None, "counts": []})
        self.assertEqual(db._sync_state(), {"seen": {}, "counts": {}})
        db._save_sync_state({"seen": {"bad": 123, "ok": "2099-01-01"},
                             "counts": {"bad": "x", "u|2099-01-01": 2}})
        saved = db._sync_state()
        self.assertEqual(saved["seen"], {"ok": "2099-01-01"})
        self.assertEqual(saved["counts"], {"u|2099-01-01": 2})

        state = {}
        rows, skipped = db.community_rows([(
            {"id": "c-1", "package": "org.example.app", "date": "2026-09-26T10:00:00", "notes": True},
            "rep-1", "2026-09-26T10:00:00Z",
        )], state)
        self.assertEqual(rows, [])
        self.assertEqual(skipped, [("c-1", "bad field notes")])

    def test_http_exception_and_keychain_errors_wrap_as_db_error(self):
        import http.client
        import subprocess
        os.environ["FRAME_CONTROL_KEY"] = "secret"
        with mock.patch.object(db._opener, "open", side_effect=http.client.BadStatusLine("bad")):
            with self.assertRaises(db.DBError):
                db._request("/v1/reports")
            os.environ["POSTHOG_PERSONAL_API_KEY"] = "phx_key"
            os.environ["FRAME_CONTROL_POSTHOG_PROJECT"] = "123"
            with self.assertRaises(db.DBError):
                db._posthog_query("SELECT 1")

        # Whitespace-only env keys and keychain OSError/TimeoutExpired must raise DBError.
        os.environ["FRAME_CONTROL_KEY"] = "   "
        os.environ["POSTHOG_PERSONAL_API_KEY"] = "   "
        with mock.patch.object(db.frame_host, "MAC", True), \
             mock.patch.object(db.subprocess, "run", side_effect=subprocess.TimeoutExpired("security", 10)):
            with self.assertRaises(db.DBError):
                db.key()
            with self.assertRaises(db.DBError):
                db.posthog_personal_key()

    def test_unhashable_ids_and_non_dict_add_are_guarded(self):
        with self.assertRaises(ValueError):
            db.add("not-a-dict")
        # Mirror with an unhashable id must not crash load().
        with open(db.MIRROR, "w", encoding="utf-8") as f:
            json.dump({"reports": [{"id": ["unhashable"], "package": "org.example.app"}]}, f)
        db._mem.update(at=0, reports=None, source=None)
        self.assertEqual(len(db.load()), 1)

        os.environ["FRAME_CONTROL_KEY"] = "secret"
        with mock.patch.object(db, "_request", return_value={
            "reports": [{"id": ["bad"], "package": "org.bad"}, {"id": "ok-1", "package": "org.ok"}],
            "next": None,
        }):
            fetched = db.fetch_all()
        self.assertEqual([r["id"] for r in fetched], ["ok-1"])

        with mock.patch.object(db, "_request", side_effect=db.DBError("offline")):
            db.add({"id": "q-1", "package": "org.example.app", "date": "2026-09-26T10:00:00", "rating": "works"})
        with mock.patch.object(db, "_request", return_value={"rejected": [{"bad": 1}, "q-1"]}):
            self.assertEqual(db.flush(), 0)

    def test_community_rows_corrupt_counts_and_sync_malformed_response(self):
        state = {"seen": {}, "counts": {"rep-1|2026-09-26": "corrupt"}}
        rows, skipped = db.community_rows([(
            {"id": "c-2", "package": "org.example.app", "date": "2026-09-26T10:00:00", "rating": "works"},
            "rep-1", "2026-09-26T10:00:00Z",
        )], state)
        self.assertEqual([r["id"] for r in rows], ["c-2"])
        self.assertEqual(skipped, [])
        self.assertEqual(state["counts"]["rep-1|2026-09-26"], 1)

        os.environ["FRAME_CONTROL_KEY"] = "secret"
        for bad_res in (None, [], {"results": "not-a-list"}):
            with mock.patch.object(db, "_posthog_query", return_value=bad_res):
                with self.assertRaises(db.DBError):
                    db.sync(dry_run=True)

    def test_main_import_and_export_guard_missing_args_and_malformed_backup(self):
        with mock.patch.object(db.sys, "argv", ["frame_compat_db.py", "export"]):
            with self.assertRaises(SystemExit):
                db.main()
        bad_file = os.path.join(db.STATE, "bad-backup.json")
        with open(bad_file, "w", encoding="utf-8") as f:
            json.dump({"reports": "not-a-list"}, f)
        with mock.patch.object(db.sys, "argv", ["frame_compat_db.py", "import", bad_file]):
            with self.assertRaises(SystemExit) as cm:
                db.main()
        self.assertIn("error:", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
