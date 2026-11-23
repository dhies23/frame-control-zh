"""Hardening tests for apk-catalog/reports.py and apk-catalog/build.py."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


CAT = Path(__file__).resolve().parents[1] / 'apk-catalog'
sys.path.insert(0, str(CAT))
try:
    import reports
    import build
finally:
    sys.path.pop(0)


class ApkCatalogHardeningTests(unittest.TestCase):
    def test_reports_handles_malformed_rows_mixed_dates_unhashable_ratings_and_non_str_notes(self):
        # 1. about_app tolerates non-dict and non-str notes
        self.assertFalse(reports.about_app('not-a-dict'))
        self.assertTrue(reports.about_app({'result': 'install_failed', 'notes': 12345}))

        # 2. verdict handles mixed int/str dates, non-str notes, unhashable rating, and non-dict rows
        mixed = [
            'garbage',
            {'date': 20261001, 'result': 'runs', 'notes': 99, 'version': '1.0'},
            {'date': '2026-10-02', 'rating': ['unhashable'], 'notes': ['list-note'], 'version': '1.1'},
            {'date': '2026-10-03', 'rating': 'works', 'notes': 42, 'version': '1.2'},
        ]
        v, lines = reports.verdict(mixed)
        self.assertEqual(v, 'works')
        self.assertTrue(lines and '2026-10-03' in lines[0])

        # Unhashable rating as best row does not raise TypeError
        v2, lines2 = reports.verdict([{'date': 123, 'rating': ['bad'], 'notes': {'k': 'v'}}])
        self.assertEqual(v2, 'no')
        self.assertTrue(lines2)

        # Non-list/tuple input returns None safely
        self.assertIsNone(reports.verdict('not-a-list'))

        # 3. by_package ignores non-dict, missing, or non-str package keys
        grouped = reports.by_package([
            'bad',
            {'result': 'runs'},
            {'package': ['unhashable'], 'result': 'runs'},
            {'package': 'com.example.ok', 'result': 'runs'},
        ])
        self.assertEqual(list(grouped.keys()), ['com.example.ok'])

    def test_build_helpers_guard_unicode_versions_non_str_types_and_missing_marker(self):
        # 1. ver_tuple rejects non-str and non-ASCII digits
        self.assertIsNone(build.ver_tuple(123))
        self.assertIsNone(build.ver_tuple('١.٢'))
        self.assertEqual(build.ver_tuple('1.11.0'), (1, 11))

        # 2. classify tolerates string/bool minSdkVersion, non-str ABIs, and unhashable frameworks
        verdict, reasons = build.classify(
            {'usesSdk': {'minSdkVersion': '31'}, 'nativecode': ['armeabi-v7a', 123]},
            {'frameworks': [['nested'], 'sdl']},
            {'features': 'not-a-list'},
        )
        self.assertEqual(verdict, 'no')
        self.assertTrue(any('API 31' in r for r in reasons))

        # 3. load_catalog raises ValueError (not IndexError) when window.APPS= marker is missing
        with tempfile.TemporaryDirectory() as folder:
            bad_js = Path(folder) / 'apps.js'
            bad_js.write_text('console.log("no catalog here");', encoding='utf-8')
            with self.assertRaises(ValueError):
                build.load_catalog(str(bad_js))

            # jsonl skips blank lines and closes file cleanly with UTF-8
            jl = Path(folder) / 'scan.jsonl'
            jl.write_text('{"pkg": "com.example.a", "ok": true}\n\n', encoding='utf-8')
            self.assertEqual(build.jsonl(str(jl))['com.example.a']['ok'], True)


if __name__ == '__main__':
    unittest.main()
