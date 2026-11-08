"""Recording titles stored on the capture machine, separate from media paths."""
import base64
import json
import shlex
import threading
import unicodedata

TITLE_LIMIT = 120
_LOCK = threading.Lock()


def validate_title(value):
    if not isinstance(value, str):
        raise ValueError('Title must be text.')
    if any(unicodedata.category(c).startswith('C') or c in '\u2028\u2029' for c in value):
        raise ValueError('Title must not contain control characters.')
    value = value.strip()
    if len(value) > TITLE_LIMIT:
        raise ValueError(f'Title must be at most {TITLE_LIMIT} characters.')
    return value


# This small program runs on the recording machine, even without Whisper.
_REMOTE = r'''
import json, os, pathlib, re
p = json.loads(PAYLOAD)
try:
    directory = pathlib.Path(p['directory'])
    store = directory / '.titles.json'
    try:
        titles = json.loads(store.read_text(encoding='utf-8'))
    except FileNotFoundError:
        titles = {}
    if not isinstance(titles, dict):
        raise ValueError('Invalid titles file.')
    result = {'ok': True, 'titles': titles}
    if p['action'] != 'list':
        target = pathlib.Path(p['file'])
        if target.parent != directory or not re.fullmatch(r'call-\d{8}-\d{6}\.mkv', target.name):
            raise ValueError('Not a recording in the recordings directory.')
        if not target.is_file():
            raise FileNotFoundError()
        if p['action'] == 'set':
            if p['title']:
                titles[target.name] = p['title']
            else:
                titles.pop(target.name, None)
            staging = store.with_suffix('.json.tmp')
            try:
                staging.write_text(json.dumps(titles, ensure_ascii=True), encoding='utf-8')
                os.replace(staging, store)
            finally:
                if staging.exists():
                    staging.unlink()
        result = {'ok': True, 'file': p['file'], 'title': titles.get(target.name, '')}
except FileNotFoundError:
    result = {'ok': False, 'error': 'Recording not found.'}
except PermissionError:
    result = {'ok': False, 'error': 'Permission denied reading or saving recording titles.'}
except (ValueError, TypeError):
    result = {'ok': False, 'error': 'Could not read recording titles or invalid recording path.'}
except OSError:
    result = {'ok': False, 'error': 'Could not save recording titles on that machine.'}
print(json.dumps(result, ensure_ascii=True))
'''


def request(rec, action, path='', title=''):
    try:
        if action == 'set':
            title = validate_title(title)
        if rec.os_name == 'windows':
            from capture_windows import resolved_dir
            from capture_windows_wasapi import _q
            directory, python, quote = resolved_dir(rec), 'python', _q
        else:
            directory, python, quote = rec.rec_dir(), 'python3', shlex.quote
        payload = json.dumps({'action': action, 'directory': directory, 'file': path, 'title': title})
        script = _REMOTE.replace('PAYLOAD', repr(payload))
        encoded = base64.b64encode(script.encode()).decode()
        command = 'import base64;exec(base64.b64decode(' + repr(encoded) + '))'
        with _LOCK:
            code, out = rec.run(f'{python} -c {quote(command)}', timeout=30)
        if code != 0:
            return {'ok': False, 'error': 'Could not reach the recording machine or run Python for titles.'}
        return json.loads(out.strip())
    except (OSError, RuntimeError, ValueError, TypeError):
        return {'ok': False, 'error': 'Could not read or save titles on the recording machine.'}
