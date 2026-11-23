"""Frame Control's compatibility database: a private Lakebed capsule
(compat-db/, https://frame-compat.lakebed.app) that only this app can read or
write, using a key from $FRAME_CONTROL_KEY or the macOS Keychain (service
frame-control-compat-db, account app-key). Without one (anyone but the
maintainer), reports stay local.

New reports go to a local outbox first and are sent from there, so nothing is
lost offline. A mirror of every report is kept for offline reads. Both live in
frame_host.data_dir('compat-db'). Python stdlib only.

Everyone else can opt in to sharing (the Privacy panel): their reports then
also go to PostHog as compat_report events (frame_telemetry.py), and the
maintainer's `sync` pulls them into the database, at most SYNC_DAILY_CAP per
reporter per day, marked via=community[-probe|-install].

CLI: python3 ui/frame_compat_db.py {count|export FILE|import FILE|flush|sync}
(import restores a backup; reports already in the database are skipped.)
"""
import http.client, json, os, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request, uuid

import frame_host
import frame_telemetry

URL = os.environ.get('FRAME_COMPAT_DB_URL', 'https://frame-compat.lakebed.app')
KEYCHAIN = ('frame-control-compat-db', 'app-key')
STATE = str(frame_host.data_dir('compat-db'))
OUTBOX = os.path.join(STATE, 'compat-outbox.jsonl')
MIRROR = os.path.join(STATE, 'compat-mirror.json')
FIELDS = ('package', 'version', 'result', 'rating', 'notes', 'via', 'date', 'steamos', 'lepton', 'runtime',
          'label', 'source')
TTL = 60  # seconds a fetched copy is reused
_lock = threading.RLock()
_load_lock = threading.Lock()  # refreshing the cached reports (load); separate from _lock, which flush takes
_mem = {'at': 0, 'reports': None, 'source': None}


class DBError(RuntimeError):
    pass


def key():
    k = (os.environ.get('FRAME_CONTROL_KEY') or '').strip()
    if k:
        return k
    p = None
    if frame_host.MAC:
        try:
            p = subprocess.run(['security', 'find-generic-password', '-s', KEYCHAIN[0], '-a', KEYCHAIN[1], '-w'],
                               capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            p = None
    if p is None or p.returncode != 0 or not p.stdout.strip():
        raise DBError('No compatibility-database key (set FRAME_CONTROL_KEY, or on macOS the Keychain '
                      f'item service {KEYCHAIN[0]}, account {KEYCHAIN[1]})')
    return p.stdout.strip()


def shared():
    """Whether reports reach the shared database. Without the key (anyone but the
    maintainer), reports stay in this computer's outbox and ratings come from the catalogue."""
    try:
        key()
        return True
    except DBError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: urllib would copy the key header to the new host."""
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _request(path, body=None, timeout=20):
    req = urllib.request.Request(URL + path, method='POST' if body is not None else 'GET',
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'x-frame-control-key': key(), 'content-type': 'application/json',
                                          'user-agent': 'FrameControl/1'})
    try:
        with _opener.open(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise DBError(f'compatibility database said HTTP {e.code}')
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError) as e:
        raise DBError(f"can't reach the compatibility database: {e}")


def _from_row(row):
    r = {k: row.get(k) for k in FIELDS if k != 'date'}
    r['date'] = row.get('reportedAt')
    r['id'] = row.get('clientId') or row.get('id')
    return r


def fetch_all():
    """Every report from the database (paged), deduplicated."""
    seen, out, since = set(), [], ''
    for _ in range(200):
        page = _request('/v1/reports?since=' + urllib.parse.quote(since))
        if not isinstance(page, dict):
            raise DBError('unexpected compatibility database response')
        rows = page.get('reports', [])
        if not isinstance(rows, list):
            raise DBError('unexpected compatibility database response')
        for row in rows:
            rid = row.get('id') if isinstance(row, dict) else None
            if isinstance(rid, str) and rid and rid not in seen:
                seen.add(rid)
                out.append(_from_row(row))
        nxt = page.get('next')
        if nxt is not None and not isinstance(nxt, str):
            raise DBError('unexpected compatibility database response')
        if not nxt or nxt == since:
            break
        since = nxt
    return out


RESULTS = ('runs', 'crashes', 'install_failed', 'instance_failed')
RATINGS = ('works', 'issues', 'broken')


def problem(r):
    """Why the server would reject this report, or None. Mirrors compat-db/server/index.ts."""
    if not isinstance(r, dict):
        return 'not an object'
    for k in ('package', 'id', 'date'):
        if not r.get(k) or not isinstance(r[k], str):
            return f'missing {k}'
    if r.get('result') not in (None, '', *RESULTS):
        return f"bad result {r['result']!r}"
    if r.get('rating') not in (None, '', *RATINGS):
        return f"bad rating {r['rating']!r}"
    return None


def _quarantine(lines, why):
    """Keep what can't be sent, with the reason, instead of dropping it."""
    os.makedirs(STATE, exist_ok=True)
    with open(OUTBOX + '.rejected', 'a', encoding='utf-8') as f:
        for line in lines:
            f.write(json.dumps({'why': why, 'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'line': line}) + '\n')


def _outbox():
    """Queued reports. Unreadable or invalid lines move to the .rejected file."""
    with _lock:
        if not os.path.exists(OUTBOX):
            return []
        good, bad = [], []
        with open(OUTBOX, encoding='utf-8', errors='replace') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    bad.append((line.rstrip('\n'), 'unreadable JSON'))
                    continue
                why = problem(r)
                (bad.append((line.rstrip('\n'), why)) if why else good.append(r))
        if bad:
            for line, why in bad:
                _quarantine([line], why)
            _write_outbox(good)
        return good


def _write_outbox(rows):
    os.makedirs(STATE, exist_ok=True)
    tmp = OUTBOX + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + '\n' for r in rows)
        os.replace(tmp, OUTBOX)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def flush():
    """Send queued reports. Sent ones leave the outbox; ones the server rejects go to
    the .rejected file; on a network error the rest stay queued. Returns how many are left."""
    with _lock:
        pending = _outbox()
        while pending:
            batch = pending[:100]
            res = _request('/v1/reports', {'reports': [{**r, 'clientId': r['id']} for r in batch]})
            if not isinstance(res, dict):
                raise DBError('unexpected compatibility database response')
            raw_rejected = res.get('rejected')
            if raw_rejected is not None and not isinstance(raw_rejected, list):
                raise DBError('unexpected compatibility database response')
            rejected = {x for x in (raw_rejected or []) if isinstance(x, str)}
            if rejected:
                _quarantine([json.dumps(r) for r in batch if r['id'] in rejected], 'rejected by the server')
            pending = pending[100:]
            _write_outbox(pending)
        return len(pending)


def _read_mirror():
    try:
        with open(MIRROR, encoding='utf-8') as f:
            data = json.load(f)
        rows = data.get('reports', []) if isinstance(data, dict) else []
        if not isinstance(rows, list):
            return []
        return [r for r in rows if isinstance(r, dict) and r.get('package')]
    except (OSError, ValueError):
        return []


def _save_mirror(reports):
    """Keep a copy for offline use. Failing to write it mustn't fail the read."""
    tmp = MIRROR + '.tmp'
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'fetched': time.strftime('%Y-%m-%dT%H:%M:%S'), 'reports': reports}, f)
        os.replace(tmp, MIRROR)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def load():
    """All reports: the database (cached for TTL s), else the offline mirror; plus unsent ones."""
    # The page asks for the catalogue and the reports at once: one refresh at a
    # time, so they share a fetch and never write the mirror's .tmp together.
    with _load_lock:
        now = time.time()
        if _mem['reports'] is None or now - _mem['at'] > TTL:
            try:
                if not shared():
                    raise DBError('no key')
                try:
                    flush()
                except Exception:
                    pass  # sending can fail for any reason; reading must still work
                reports, source = fetch_all(), 'lakebed'
                _save_mirror(reports)
            except DBError:
                reports, source = _read_mirror(), 'mirror'
            _mem.update(at=now, reports=reports, source=source)
        cached = list(_mem['reports'])
    sent = {r.get('id') for r in cached if isinstance(r.get('id'), str)}
    return cached + [r for r in _outbox() if r['id'] not in sent]


def add(report):
    """Validate, queue, then try to send. Never raises once the report is queued."""
    if not isinstance(report, dict):
        raise ValueError('report not saved: not an object')
    r = {k: report.get(k) for k in FIELDS}
    r['id'] = report.get('id') or str(uuid.uuid4())
    why = problem(r)
    if why:
        raise ValueError(f'report not saved: {why}')
    os.makedirs(STATE, exist_ok=True)
    with _lock, open(OUTBOX, 'a', encoding='utf-8') as f:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
    try:
        if shared():
            flush()
            _mem['at'] = 0  # refetch on next load
        else:
            frame_telemetry.compat_report(r)  # only if this person opted in to sharing
    except Exception:
        pass  # stays queued; load() shows it and a later call sends it
    return r


# ---- community reports: PostHog -> the database (maintainer only) ---------------

POSTHOG_KEYCHAIN = ('frame-control-posthog', 'personal-api-key')
SYNC_STATE = os.path.join(STATE, 'posthog-sync.json')
SYNC_DAILY_CAP = 30
COMMUNITY_VIA = {'user': 'community', 'probe': 'community-probe', 'install': 'community-install'}


def posthog_personal_key():
    k = (os.environ.get('POSTHOG_PERSONAL_API_KEY') or '').strip()
    if k:
        return k
    if frame_host.MAC:
        try:
            p = subprocess.run(['security', 'find-generic-password', '-s', POSTHOG_KEYCHAIN[0], '-a',
                                POSTHOG_KEYCHAIN[1], '-w'], capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            p = None
        if p is not None and p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    raise DBError('No PostHog personal API key (set POSTHOG_PERSONAL_API_KEY, or on macOS the Keychain '
                  f'item service {POSTHOG_KEYCHAIN[0]}, account {POSTHOG_KEYCHAIN[1]})')


def _posthog_query(sql):
    cfg = frame_telemetry.config()
    project = os.environ.get('FRAME_CONTROL_POSTHOG_PROJECT') or cfg.get('project')
    if not project:
        raise DBError('No PostHog project id (ui/telemetry.json "project", or FRAME_CONTROL_POSTHOG_PROJECT)')
    # The query API lives on the app host (us.posthog.com), not the ingestion host (us.i.posthog.com).
    host = cfg['host'].replace('.i.posthog.com', '.posthog.com')
    req = urllib.request.Request(f'{host}/api/projects/{urllib.parse.quote(str(project))}/query/', method='POST',
                                 data=json.dumps({'query': {'kind': 'HogQLQuery', 'query': sql}}).encode(),
                                 headers={'authorization': 'Bearer ' + posthog_personal_key(),
                                          'content-type': 'application/json'})
    try:
        with _opener.open(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        with e:
            raise DBError(f'PostHog said HTTP {e.code}: {e.read()[:300]!r}')
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError) as e:
        raise DBError(f"can't reach PostHog: {e}")


SYNC_OVERLAP_DAYS = 30  # re-read this far back: offline copies send late, with their original time
SYNC_PAGE = 5000


def community_rows(events, state, cap=SYNC_DAILY_CAP):
    """(reports, skipped): compat_report events as database rows. `state` ({"seen": {id: day},
    "counts": {"reporter|day": n}}) persists between syncs, so an event read twice is handled
    once and each reporter gets at most `cap` reports a day in total."""
    seen = state['seen'] if isinstance(state.get('seen'), dict) else state.setdefault('seen', {})
    if not isinstance(seen, dict):
        seen = state['seen'] = {}
    counts = state['counts'] if isinstance(state.get('counts'), dict) else state.setdefault('counts', {})
    if not isinstance(counts, dict):
        counts = state['counts'] = {}
    out, skipped = [], []
    for props, reporter, ts in events:
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except ValueError:
                props = None
        if not isinstance(props, dict):
            skipped.append((None, 'unreadable properties'))
            continue
        bad = [k for k in (*FIELDS, 'id')
               if props.get(k) is not None and (isinstance(props[k], bool) or not isinstance(props[k], (str, int, float)))]
        if bad:
            skipped.append((str(props.get('id'))[:60], f'bad field {bad[0]}'))
            continue
        r = {k: (str(props[k]) if props.get(k) is not None else None) for k in FIELDS}
        r['id'] = str(props['id']) if props.get('id') is not None else None
        if r['id'] in seen:
            continue  # handled in an earlier sync (or earlier in this one)
        r['via'] = COMMUNITY_VIA.get(r.get('via') or 'user', 'community')
        why = problem(r)
        if why:
            skipped.append((r.get('id'), why))
            continue
        day = str(ts)[:10]
        seen[r['id']] = day
        key_ = f'{reporter}|{day}'
        cur = counts.get(key_)
        cur = cur if type(cur) is int else 0
        if cur >= cap:
            skipped.append((r['id'], 'over the daily limit for one reporter'))
            continue
        counts[key_] = cur + 1
        out.append(r)
    return out, skipped


def _sync_state():
    try:
        with open(SYNC_STATE, encoding='utf-8') as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_sync_state(s):
    """Forget ids and counts older than the overlap window (plus a margin)."""
    cutoff = time.strftime('%Y-%m-%d', time.gmtime(time.time() - (SYNC_OVERLAP_DAYS + 15) * 86400))
    raw_seen = s.get('seen') if isinstance(s.get('seen'), dict) else {}
    raw_counts = s.get('counts') if isinstance(s.get('counts'), dict) else {}
    s['seen'] = {k: d for k, d in raw_seen.items() if isinstance(k, str) and isinstance(d, str) and d >= cutoff}
    s['counts'] = {k: n for k, n in raw_counts.items()
                   if isinstance(k, str) and type(n) is int and k.rsplit('|', 1)[-1] >= cutoff}
    os.makedirs(STATE, exist_ok=True)
    tmp = SYNC_STATE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(s, f)
        os.replace(tmp, SYNC_STATE)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def sync(dry_run=False):
    """Pull community reports from PostHog into the database. Returns (added, skipped).
    Reads the last SYNC_OVERLAP_DAYS each time, since events carry the time they were
    made, not when they arrived; the saved state keeps that from adding anything twice."""
    key()  # the maintainer's copy only
    state = _sync_state()
    since = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(time.time() - SYNC_OVERLAP_DAYS * 86400))
    events, after = [], f"timestamp >= toDateTime('{since}', 'UTC')"
    for _ in range(40):
        # Keyset paging: PostHog refuses OFFSET with a personal API key. The cursor is in UTC,
        # since a local time is ambiguous in the hour clocks go back.
        res = _posthog_query("SELECT properties, distinct_id, timestamp, toString(uuid), "
                             "formatDateTime(timestamp, '%Y-%m-%d %H:%i:%S.%f', 'UTC') FROM events "
                             f"WHERE event = 'compat_report' AND {after} "
                             f"ORDER BY timestamp, toString(uuid) LIMIT {SYNC_PAGE}")
        if not isinstance(res, dict):
            raise DBError('unexpected PostHog response')
        rows = res.get('results')
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise DBError('unexpected PostHog response')
        events += [row[:3] for row in rows if isinstance(row, (list, tuple)) and len(row) >= 3]
        if len(rows) < SYNC_PAGE:
            break
        last = rows[-1]
        if not isinstance(last, (list, tuple)) or len(last) < 5:
            raise DBError('unexpected PostHog response')
        last_uuid, last_ts = last[3], last[4]
        if (not isinstance(last_uuid, str) or not isinstance(last_ts, str)
                or "'" in last_uuid or "'" in last_ts):
            raise DBError('unexpected PostHog pagination cursor')
        after = (f"(timestamp > toDateTime64('{last_ts}', 6, 'UTC') OR "
                 f"(timestamp = toDateTime64('{last_ts}', 6, 'UTC') AND toString(uuid) > '{last_uuid}'))")
    rows, skipped = community_rows(events, state)
    if dry_run:
        return rows, skipped
    if rows:
        os.makedirs(STATE, exist_ok=True)
        with _lock, open(OUTBOX, 'a', encoding='utf-8') as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + '\n' for r in rows)
    # Saved before sending: the rows are in the outbox now, and flush retries them if sending fails.
    _save_sync_state(state)
    flush()  # also retries rows a failed earlier sync left in the outbox
    _mem['at'] = 0
    return rows, skipped


def main():
    cmd, *args = sys.argv[1:] or ['count']
    try:
        if cmd == 'count':
            print(len(fetch_all()))
        elif cmd == 'export':
            if not args:
                sys.exit(__doc__)
            reports = fetch_all()
            with open(args[0], 'w', encoding='utf-8') as f:
                json.dump({'exported': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'source': URL,
                           'count': len(reports), 'reports': reports}, f, indent=1)
            print(f'{len(reports)} reports -> {args[0]}')
        elif cmd == 'import':
            if not args:
                sys.exit(__doc__)
            try:
                with open(args[0], encoding='utf-8') as f:
                    backup = json.load(f)
            except (OSError, ValueError) as e:
                raise DBError(f"can't read {args[0]}: {e}")
            if not isinstance(backup, dict) or not isinstance(backup.get('reports'), list):
                raise DBError('backup file must be a JSON object with a "reports" list')
            rows = [({**{k: r.get(k) for k in FIELDS}, 'id': r.get('id')} if isinstance(r, dict) else r)
                    for r in backup['reports']]
            bad = [(r, problem(r)) for r in rows if problem(r)]
            ok = [r for r in rows if not problem(r)]
            for r, why in bad:
                pkg = r.get('package') if isinstance(r, dict) else None
                print(f"skipped {pkg!r}: {why}", file=sys.stderr)
            os.makedirs(STATE, exist_ok=True)
            with _lock, open(OUTBOX, 'a', encoding='utf-8') as f:
                f.writelines(json.dumps(r, ensure_ascii=False) + '\n' for r in ok)
            left = flush()
            print(f'{len(ok)} reports sent, {len(bad)} invalid skipped, {left} still queued; '
                  'reports already in the database were not duplicated')
        elif cmd == 'flush':
            print(f'{flush()} still queued')
        elif cmd == 'sync':
            rows, skipped = sync(dry_run='--dry-run' in args)
            for rid, why in skipped:
                print(f'skipped {rid!r}: {why}', file=sys.stderr)
            print(f"{len(rows)} community reports {'found' if '--dry-run' in args else 'added'}, "
                  f'{len(skipped)} skipped')
        else:
            sys.exit(__doc__)
    except DBError as e:
        sys.exit(f'error: {e}')


if __name__ == '__main__':
    main()
