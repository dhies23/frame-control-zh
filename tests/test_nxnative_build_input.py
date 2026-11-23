import sandbox  # noqa: F401
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
NX_DIR = ROOT / 'frame' / 'nxnative'


def load_module(name, path):
    if str(NX_DIR) not in sys.path:
        sys.path.insert(0, str(NX_DIR))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nx_build = load_module('nxnative_build_test', NX_DIR / 'build.py')
nx_input = load_module('nxnative_input_test', NX_DIR / 'input.py')


class NxNativeBuildTests(unittest.TestCase):
    def test_profile_inputs_rejects_unicode_digits_and_malformed_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'src'
            extracted = root / 'ext'
            exefs = extracted / 'ExeFS'
            exefs.mkdir(parents=True)
            nso = b'NSO0' + bytes(0x3C) + bytes.fromhex('11' * 16) + bytes(0x10)
            (exefs / 'main').write_bytes(nso)

            # Unicode Arabic-Indic digit must be rejected as ValueError
            bad_unicode = json.dumps({'modules': ['subsdk\u0661'], 'builds': {}})
            with mock.patch.object(nx_build.subprocess, 'check_output', return_value=bad_unicode):
                with self.assertRaisesRegex(ValueError, 'invalid module list'):
                    nx_build.profile_inputs(source, '010093801237C000', extracted, {})

            # Non-dict builds or non-dict build entry or non-str hash must raise ValueError (not AttributeError)
            for payload in (
                {'modules': ['main'], 'builds': ['not-a-dict']},
                {'modules': ['main'], 'builds': {'v1': ['not-a-dict']}},
                {'modules': ['main'], 'builds': {'v1': {'main': 12345}}},
            ):
                with mock.patch.object(nx_build.subprocess, 'check_output', return_value=json.dumps(payload)):
                    with self.assertRaises(ValueError):
                        nx_build.profile_inputs(source, '010093801237C000', extracted, {})

    def test_build_rejects_boolean_jobs(self):
        args = types.SimpleNamespace(jobs=True, title='010093801237C000')
        with self.assertRaisesRegex(ValueError, 'jobs must be positive'):
            nx_build.build(args)


class NxNativeInputTests(unittest.TestCase):
    def test_button_validates_key_hold_title_and_meta_before_spawning_podman(self):
        title = '010093801237C000'
        pkg = 'com.nxnative.t' + title.lower()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            meta_dir = home / 'Applications/Android' / pkg
            meta_dir.mkdir(parents=True)
            (meta_dir / 'meta.json').write_text(json.dumps({'package': pkg, 'instance': 42}), encoding='utf-8')

            with mock.patch.object(nx_input.Path, 'home', return_value=home), \
                 mock.patch.object(nx_input.subprocess, 'Popen') as popen:
                # Unknown key must raise ValueError before spawning podman
                with self.assertRaisesRegex(ValueError, 'unknown key'):
                    nx_input.button('BAD_KEY', 50, title)
                # Boolean hold must raise ValueError
                with self.assertRaisesRegex(ValueError, 'hold'):
                    nx_input.button('A', True, title)
                # Non-string title must raise ValueError
                with self.assertRaisesRegex(ValueError, 'title'):
                    nx_input.button('A', 50, 1234567890123456)
                popen.assert_not_called()

                # Malformed metadata: non-dict, bool instance, negative instance, Unicode digit instance
                for bad_meta in (
                    ['not-a-dict'],
                    {'package': pkg, 'instance': True},
                    {'package': pkg, 'instance': -5},
                    {'package': pkg, 'instance': '\u0661'},
                ):
                    (meta_dir / 'meta.json').write_text(json.dumps(bad_meta), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        nx_input.button('A', 50, title)
                popen.assert_not_called()

    def test_button_closes_stdin_and_waits_child_on_broken_pipe(self):
        title = '010093801237C000'
        pkg = 'com.nxnative.t' + title.lower()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            meta_dir = home / 'Applications/Android' / pkg
            meta_dir.mkdir(parents=True)
            (meta_dir / 'meta.json').write_text(json.dumps({'package': pkg, 'instance': 7}), encoding='utf-8')

            closed = []
            waited = []

            class FakeStdin:
                def __init__(self):
                    self.writes = 0

                def write(self, _data):
                    self.writes += 1
                    if self.writes == 2:
                        raise BrokenPipeError('pipe closed')

                def flush(self):
                    pass

                def close(self):
                    closed.append(True)

            class FakeChild:
                def __init__(self):
                    self.stdin = FakeStdin()

                def wait(self):
                    waited.append(True)
                    return 1

            with mock.patch.object(nx_input.Path, 'home', return_value=home), \
                 mock.patch.object(nx_input.time, 'sleep', return_value=None), \
                 mock.patch.object(nx_input.subprocess, 'Popen', return_value=FakeChild()):
                with self.assertRaisesRegex(RuntimeError, 'Lepton input FIFO write failed'):
                    nx_input.button('A', 10, title)
            self.assertEqual(closed, [True])
            self.assertEqual(waited, [True])


if __name__ == '__main__':
    unittest.main()
