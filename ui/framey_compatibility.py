"""Digest-scoped observations, never a trust or execution guarantee."""
import json
from functools import lru_cache
from pathlib import Path


DATA = Path(__file__).with_name('framey-compatibility.json')


@lru_cache(maxsize=2)
def _records(path):
    return json.loads(path.read_text(encoding='utf-8'))['plugins']


def lookup(plugin_id, digest, version, installed_shims=None):
    unknown = {'status': 'unverified', 'reason': 'This exact store version has not been audited on Frame.',
               'verification': [], 'blockInstall': False}
    if type(plugin_id) is not int or not isinstance(digest, str) or not isinstance(version, str):
        return unknown
    try:
        records = _records(DATA)
        matches = [p for p in records if p['id'] == plugin_id and p['sha256'] == digest and p['version'] == version]
        if len(matches) != 1:
            return unknown
        record = matches[0]
        result = {
            'status': record['status'],
            'reason': record['reason'],
            'verification': list(record['verification']),
            'blockInstall': record['blockInstall'],
        }
        required = record.get('requiredShims', [])
        if result['status'] in ('works', 'partial') and required and installed_shims is not None and (
                not isinstance(installed_shims, list) or
                any(shim not in installed_shims for shim in required)):
            result['status'] = record.get('missingShimStatus', 'partial')
            result['reason'] = ('Update or reinstall this managed plugin to add its missing Frame compatibility fix. '
                                + result['reason'])
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return unknown
