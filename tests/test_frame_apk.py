"""frame_apk against a small APK built here: binary manifest plus resource table."""
import sandbox  # noqa: F401  (first: keeps tests off real data and services)
import io
import os
import struct
import sys
import tempfile
import tracemalloc
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ui'))
import frame_apk  # noqa: E402
from apk_fixtures import apk, manifest, pool, resources  # noqa: E402,F401


class ApkInfo(unittest.TestCase):
    def read(self, data):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'app.apk')
            with open(p, 'wb') as f:
                f.write(data)
            return frame_apk.apk_info(p)

    def test_resolves_references(self):
        # string type 1: label (entry 0), version (entry 1); mipmap type 2: icon at three densities.
        arsc = resources({(1, 'fr', 0): {0: 0}, (1, '', 0): {0: 1, 1: 2},
                          (2, '', 160): {0: 3}, (2, '', 640): {0: 4}, (2, '', 0xfffe): {0: 5}})
        info = self.read(apk({
            'AndroidManifest.xml': manifest('com.example.demo', 0x7f010000, 0x7f010001, 26),
            'resources.arsc': arsc,
            'res/icon_lo.png': b'lo', 'res/icon_hi.png': b'hi', 'res/icon.xml': b'<xml/>',
            'lib/arm64-v8a/libx.so': b'', 'lib/x86_64/libx.so': b'',
        }))
        self.assertEqual(info['package'], 'com.example.demo')
        self.assertEqual(info['label'], 'App label')  # the default, not French
        self.assertEqual(info['version'], '2.1')
        self.assertEqual(info['version_code'], 210)
        self.assertEqual(info['min_sdk'], 26)
        self.assertEqual(info['abis'], ['arm64-v8a', 'x86_64'])
        self.assertEqual(info['icon_png'], b'hi')  # largest-density PNG, skipping the XML icon

    def test_missing_label_falls_back_to_package(self):
        info = self.read(apk({'AndroidManifest.xml': manifest('com.example.bare', 0x7f010000, 0x7f010001, 21)}))
        self.assertEqual(info['label'], 'com.example.bare')
        self.assertEqual(info['version'], '')
        self.assertEqual(info['abis'], [])

    def test_repacked_manifest(self):
        # Package kept only as a typed value; a foreign `label` mustn't beat android:label.
        arsc = resources({(1, '', 0): {0: 1, 1: 2}})
        info = self.read(apk({
            'AndroidManifest.xml': manifest('com.example.repacked', 0x7f010000, 0x7f010001, 24,
                                            package_raw=False, foreign_label=True),
            'resources.arsc': arsc,
        }))
        self.assertEqual(info['package'], 'com.example.repacked')
        self.assertEqual(info['label'], 'App label')
        self.assertIsNone(info['icon_png'])

    def test_rejects_non_apks(self):
        for data in (b'not a zip', apk({'classes.dex': b''}), apk({'AndroidManifest.xml': b'<manifest/>'})):
            with self.assertRaises(frame_apk.ApkError):
                self.read(data)

    def test_refuses_oversized_members(self):
        # An APK from a website mustn't make the server inflate gigabytes.
        data = apk({'AndroidManifest.xml': manifest('com.example.big', 0x7f010000, 0x7f010001, 21)})
        limit, frame_apk.MAX_MANIFEST = frame_apk.MAX_MANIFEST, 16
        try:
            with self.assertRaises(frame_apk.ApkError):
                self.read(data)
        finally:
            frame_apk.MAX_MANIFEST = limit

    def test_forged_sizes_dont_inflate_everything(self):
        # The central directory claims 1 byte; the deflated data holds 16 MB of zeros.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('AndroidManifest.xml', bytes(16 * 1024**2))
        data = bytearray(buf.getvalue())
        for sig, field in ((b'PK\x01\x02', 24), (b'PK\x03\x04', 22)):
            at = data.index(sig)
            data[at + field:at + field + 4] = struct.pack('<I', 1)
        limit, frame_apk.MAX_MANIFEST = frame_apk.MAX_MANIFEST, 1024**2
        tracemalloc.start()
        try:
            with self.assertRaises(frame_apk.ApkError):
                self.read(bytes(data))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
            frame_apk.MAX_MANIFEST = limit
        self.assertLess(peak, 8 * 1024**2)

    def test_damaged_download_is_an_apk_error(self):
        # Corrupt deflate data used to escape as zlib.error, which the upload path didn't catch
        # (a 500, and no install report) and frame_android.install reported as 'other'.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('AndroidManifest.xml', manifest('com.example.bad', 0x7f010000, 0x7f010001, 21) * 20)
        data = bytearray(buf.getvalue())
        at = data.index(b'PK\x03\x04')
        start = at + 30 + sum(struct.unpack_from('<HH', data, at + 26))  # past the name and extra field
        for i in range(start, start + 40):
            data[i] ^= 0x5a
        with self.assertRaisesRegex(frame_apk.ApkError, 'could not read the APK manifest'):
            self.read(bytes(data))

    def test_refuses_compression_android_cant_read(self):
        for method in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', method) as z:
                z.writestr('AndroidManifest.xml', manifest('com.example.odd', 0x7f010000, 0x7f010001, 21))
            with self.assertRaisesRegex(frame_apk.ApkError, 'compression'):
                self.read(buf.getvalue())

    def test_reference_cycles_and_fan_out_are_bounded(self):
        res = frame_apk.Resources(b'')
        ref = frame_apk.T_REF
        res.entries = {1: [('', 0, ref, 1)] * 5}  # five references to itself
        self.assertEqual(res.values(1), [])
        # Five references at each of five hops: 3125 leaves without a budget.
        res.entries = {i: [('', 0, ref, i + 1)] * 5 for i in range(1, 6)}
        res.entries[6] = [('', 0, frame_apk.T_STRING, 0)]
        self.assertEqual(len(res.values(1)), frame_apk.MAX_VALUES)
        # Forty references at each hop round a four-id cycle: millions of dead ends.
        looked = []

        class Counting(dict):
            def get(self, key, default=None):
                looked.append(key)
                return dict.get(self, key, default)
        res.entries = Counting({i: [('', 0, ref, i % 4 + 1)] * 40 for i in range(1, 5)})
        self.assertEqual(res.values(1), [])
        self.assertLess(len(looked), frame_apk.MAX_STEPS + 10)

    def test_crafted_huge_counts_and_bad_headers_fail_fast_as_apk_error(self):
        # String pool with count=0x10000000 in a 28-byte chunk must be rejected before unpacking.
        bad_pool = struct.pack('<HHIIIII', 0x0001, 28, 28, 0x10000000, 0, 0, 28) + b'\0' * 4
        bad_manifest = struct.pack('<HHI', 0x0003, 8, 8 + len(bad_pool)) + bad_pool
        with self.assertRaises(frame_apk.ApkError):
            frame_apk.manifest_elements(bad_manifest)

        # Start tag with asize=0 and count=100 must be rejected as corrupt instead of re-reading.
        sp = pool(['manifest', 'package', 'com.example.bad'])
        start_tag = struct.pack('<HHI', 0x0102, 16, 36) + b'\0' * 8 + struct.pack('<4xIHHH', 0, 0, 0, 100) + b'\0' * 10
        zero_asize = struct.pack('<HHI', 0x0003, 8, 8 + len(sp) + len(start_tag)) + sp + start_tag
        with self.assertRaises(frame_apk.ApkError):
            frame_apk.manifest_elements(zero_asize)

        # Resource table type chunk claiming count=0x10000000 in a 40-byte chunk must raise ApkError.
        type_chunk = struct.pack('<HHI', 0x0201, 36, 40) + struct.pack('<BB2xII', 1, 0, 0x10000000, 40) + b'\0' * 20
        pkg_chunk = struct.pack('<HHI', 0x0200, 12, 12 + len(type_chunk)) + struct.pack('<I', 0x7f) + type_chunk
        arsc = struct.pack('<HHI', 0x0002, 12, 12 + len(pkg_chunk)) + struct.pack('<I', 1) + pkg_chunk
        with self.assertRaises(frame_apk.ApkError):
            frame_apk.Resources(arsc)

        # Resmap chunk with hsize > size must be skipped by _chunks without negative unpack count.
        bad_resmap = struct.pack('<HHI', 0x0180, 16, 12) + b'\0' * 4
        bad_hdr_manifest = struct.pack('<HHI', 0x0003, 8, 8 + len(bad_resmap)) + bad_resmap
        self.assertEqual(frame_apk.manifest_elements(bad_hdr_manifest), [])

    def test_icon_fallback_skips_oversized_candidate(self):
        arsc = resources({(1, '', 0): {0: 1, 1: 2}, (2, '', 160): {0: 3}, (2, '', 640): {0: 4}})
        data = apk({
            'AndroidManifest.xml': manifest('com.example.icon', 0x7f010000, 0x7f010001, 26),
            'resources.arsc': arsc,
            'res/icon_hi.png': b'X' * 64,
            'res/icon_lo.png': b'lo',
        })
        limit, frame_apk.MAX_ICON = frame_apk.MAX_ICON, 16
        try:
            info = self.read(data)
        finally:
            frame_apk.MAX_ICON = limit
        self.assertEqual(info['icon_png'], b'lo')

        # Also falls back across ic_launcher PNGs when the largest exceeds MAX_ICON.
        fallback_apk = apk({
            'AndroidManifest.xml': manifest('com.example.launcher', 0x7f010000, 0x7f010001, 26),
            'res/drawable-xxxhdpi/ic_launcher.png': b'Y' * 64,
            'res/drawable-mdpi/ic_launcher.png': b'ok',
        })
        limit, frame_apk.MAX_ICON = frame_apk.MAX_ICON, 16
        try:
            info2 = self.read(fallback_apk)
        finally:
            frame_apk.MAX_ICON = limit
        self.assertEqual(info2['icon_png'], b'ok')


if __name__ == '__main__':
    unittest.main()
