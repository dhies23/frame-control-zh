"""How compatibility reports turn into a verdict. The reports themselves live
in Frame Control's private database (ui/frame_compat_db.py, a Lakebed capsule
in compat-db/); this module is the pure logic shared by the build and the app.

A report: package, version, result (runs | crashes | install_failed |
instance_failed, from an automated test), rating (works | issues | broken, from
a person), notes, via (harness | probe | user), date, steamos, lepton, runtime.
Newest wins, and a person's rating beats an automated result.

An install_failed report saying the file had no arm64-v8a build (or needed a
newer Android) is about that one APK file, often the wrong per-ABI download of
an app that has an arm64 build, so it says nothing about the app and is left out.
"""
import re

FILE_FAULT = re.compile(r'no arm64-v8a build|needs Android API')


def about_app(r):
    """False for reports about one wrong APK file rather than the app itself."""
    if not isinstance(r, dict):
        return False
    notes = r.get('notes')
    return not (r.get('result') == 'install_failed' and not r.get('rating')
                and isinstance(notes, str) and FILE_FAULT.search(notes))


def verdict(reports):
    """(verdict, summary lines) for one package's reports, or None."""
    if not isinstance(reports, (list, tuple)):
        return None
    reports = [r for r in reports if about_app(r)]
    if not reports:
        return None
    rs = sorted(reports, key=lambda r: r.get('date') if isinstance(r.get('date'), str) else '')
    people = [r for r in rs if isinstance(r.get('rating'), str) and r['rating']]
    best = people[-1] if people else rs[-1]
    kind = best.get('rating') or best.get('result')
    v = {'works': 'works', 'runs': 'works', 'issues': 'maybe'}.get(kind, 'no') if isinstance(kind, str) else 'no'
    n_ok = sum(
        (k in ('works', 'runs'))
        for r in rs
        for k in (r.get('rating') or r.get('result'),)
        if isinstance(k, str)
    )
    date_str = best['date'][:10] if isinstance(best.get('date'), str) else ''
    notes = best.get('notes')
    note_suffix = f' – {notes}' if isinstance(notes, str) and notes else ''
    lines = [f"Reported on a Frame {date_str} (v{best.get('version')}): {kind}{note_suffix}"]
    if len(rs) > 1:
        lines.append(f'{len(rs)} reports, {n_ok} working')
    return v, lines


def by_package(reports):
    out = {}
    if not isinstance(reports, (list, tuple)):
        return out
    for r in reports:
        if isinstance(r, dict) and isinstance(r.get('package'), str) and r['package']:
            out.setdefault(r['package'], []).append(r)
    return out
