"""The iOS Frame bundle must recover transient pinned package downloads."""
import hashlib
import http.client
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error
import io


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('kdeconnect_fetch', ROOT / 'frame/kdeconnect/fetch.py')
fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch)


class VerifiedDownload(unittest.TestCase):
    def test_remote_disconnect_retries_then_checks_hash(self):
        data = b'pinned package'
        digest = hashlib.sha256(data).hexdigest()
        with patch.object(fetch.urllib.request, 'urlopen', side_effect=[
                http.client.RemoteDisconnected('closed'), io.BytesIO(data)]) as open_url, \
                patch.object(fetch.time, 'sleep') as sleep:
            self.assertEqual(fetch.download_verified('https://example.invalid/package', digest), data)
        self.assertEqual(open_url.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_permanent_http_error_and_wrong_hash_fail_closed(self):
        url = 'https://example.invalid/package'
        missing = urllib.error.HTTPError(url, 404, 'missing', {}, None)
        with patch.object(fetch.urllib.request, 'urlopen', side_effect=missing) as open_url, \
                patch.object(fetch.time, 'sleep') as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                fetch.download_verified(url, '0' * 64)
        open_url.assert_called_once()
        sleep.assert_not_called()
        with patch.object(fetch.urllib.request, 'urlopen', return_value=io.BytesIO(b'changed')) as open_url:
            with self.assertRaisesRegex(SystemExit, 'checksum mismatch'):
                fetch.download_verified(url, '0' * 64)
        open_url.assert_called_once()

    def test_exhausted_transient_error_is_not_silenced(self):
        with patch.object(fetch.urllib.request, 'urlopen', side_effect=http.client.RemoteDisconnected('closed')) as open_url, \
                patch.object(fetch.time, 'sleep') as sleep:
            with self.assertRaises(http.client.RemoteDisconnected):
                fetch.download_verified('https://example.invalid/package', '0' * 64)
        self.assertEqual(open_url.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_hardening_rejects_non_https_and_traversal_and_cleans_stale_dirs(self):
        import tempfile
        with self.assertRaises(ValueError):
            fetch.download_verified('http://example.invalid/package', '0' * 64)

        class DowngradeResponse(io.BytesIO):
            def geturl(self):
                return 'http://example.invalid/downgrade'

        with patch.object(fetch.urllib.request, 'urlopen', return_value=DowngradeResponse(b'x')):
            with self.assertRaises(ValueError):
                fetch.download_verified('https://example.invalid/package', hashlib.sha256(b'x').hexdigest())

        with tempfile.TemporaryDirectory() as tmp:
            pkg_dir = Path(tmp) / 'packages'
            pkg_dir.mkdir()
            stale_dir = pkg_dir / 'stale-subdir'
            stale_dir.mkdir()
            data = b'good'
            good_sha = hashlib.sha256(data).hexdigest()
            (pkg_dir / 'pkg.tar.zst').write_bytes(data)

            # Traversal filename must be rejected
            with patch.object(fetch, 'PACKAGES', pkg_dir), \
                    patch.object(fetch, 'manifest', return_value={'release': 'https://example.invalid/', 'packages': [{'file': '../evil', 'sha256': good_sha}]}):
                with self.assertRaises(ValueError):
                    fetch.fetch()

            # Unexpected directory inside PACKAGES must be cleaned up without IsADirectoryError
            with patch.object(fetch, 'PACKAGES', pkg_dir), \
                    patch.object(fetch, 'manifest', return_value={'release': 'https://example.invalid/', 'packages': [{'file': 'pkg.tar.zst', 'sha256': good_sha}]}):
                kept = fetch.fetch()
            self.assertEqual(kept, [pkg_dir / 'pkg.tar.zst'])
            self.assertFalse(stale_dir.exists())


if __name__ == '__main__':
    unittest.main()
