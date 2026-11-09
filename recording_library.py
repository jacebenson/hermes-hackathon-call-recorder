"""One backend library, with durable per-file copy receipts and source provenance."""
from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
import threading
import tempfile
import shutil
import uuid
from datetime import datetime, timezone

MAX_BROWSER_BYTES = 32 * 1024 * 1024
BROWSER_MIMES = {'audio/webm', 'audio/ogg', 'audio/mp4'}
from pathlib import Path
from urllib.parse import quote

from recorder import Recorder

_LOCK = threading.RLock()
_ERRORS = {}
_RETRIES = {}
_RETRY_GATE = threading.Lock()
_NAME = re.compile(r'call-\d{8}-\d{6}\.mkv')
_SUFFIXES = ('.mkv', '.transcript.md', '.live.txt')


class RecordingLibrary:
    def __init__(self, root: Path | None = None):
        home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')
        self.root = root or home / 'plugin-data' / 'call-recorder'

    def _entries(self):
        for receipt in sorted(self.root.glob('*/call-*.library.json')):
            # A broken receipt must not make unrelated history disappear.
            try:
                entry = json.loads(receipt.read_text(encoding='utf-8'))
                yield receipt, entry
            except (OSError, ValueError):
                continue

    @staticmethod
    def _save(receipt, entry):
        staging = receipt.with_suffix('.tmp')
        staging.write_text(json.dumps(entry, indent=2), encoding='utf-8')
        staging.replace(receipt)

    def store_browser(self, data: bytes, mime: str) -> dict:
        """Decode a bounded audio-only upload into one mono track; never trust its name."""
        if not data or len(data) > MAX_BROWSER_BYTES:
            return {'ok': False, 'error': 'Audio must be between 1 byte and 32 MiB.'}
        if mime.split(';', 1)[0].lower() not in BROWSER_MIMES:
            return {'ok': False, 'error': 'Use WebM, Ogg or MP4 microphone audio.'}
        demuxer = {'audio/webm': 'matroska', 'audio/ogg': 'ogg', 'audio/mp4': 'mov'}[mime.split(';', 1)[0].lower()]
        directory = self.root / ('browser-' + uuid.uuid4().hex)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.browser-', dir=self.root) as work:
                source = Path(work) / 'input'
                source.write_bytes(data)
                probe = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                        '-f', demuxer, '-show_streams', '-of', 'json',
                                        str(source)], capture_output=True, timeout=20, check=True)
                streams = json.loads(probe.stdout)['streams']
                if len(streams) != 1 or streams[0].get('codec_type') != 'audio':
                    raise ValueError('Expected one audio stream.')
                name = datetime.now(timezone.utc).strftime('call-%Y%m%d-%H%M%S.mkv')
                output = Path(work) / name
                decoded = subprocess.run(['ffmpeg', '-nostdin', '-v', 'warning', '-xerror',
                                '-protocol_whitelist', 'file,pipe', '-f', demuxer, '-i', str(source),
                                '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '48000',
                                '-t', '7201', '-c:a', 'libopus', '-b:a', '64k',
                                '-metadata:s:a:0', 'title=microphone', str(output)],
                               capture_output=True, timeout=120, check=True)
                # FFmpeg may return zero after recovering only the first clusters.
                # Do not confuse its known EOF Opus-header parser warning with
                # demuxer evidence that the input itself ended prematurely.
                diagnostics = (probe.stderr + decoded.stderr).decode(errors='replace').lower()
                if re.search(r'ended prematurely|truncat|corrupt|invalid data|'
                             r'error (?:reading|during demuxing)|resync failed|'
                             r'extends beyond end|exceeds containing', diagnostics):
                    raise ValueError('Incomplete or corrupt microphone audio.')
                duration = Recorder().duration(str(output))
                if duration is None or not 0 < duration <= 7200:
                    raise ValueError('Duration must be at most two hours.')
                directory.mkdir(mode=0o700)
                target = directory / name
                output.replace(target)
                target.chmod(0o600)
                entry = {'machine': 'browser', 'host': None, 'source': str(target),
                         'file': str(target), 'recordings_dir': str(directory), 'os_name': 'linux',
                         'ready': True, 'completed': ['.mkv'], 'error': '',
                         'origin': 'browser', 'capture_mode': 'browser',
                         'tracks': ['microphone'], 'speaker_separated': False}
                self._save(target.with_suffix('.library.json'), entry)
                return {'ok': True, 'file': str(target), 'stored': True,
                        'duration': duration, 'capture_mode': 'browser', 'tracks': ['microphone'],
                        'speaker_separated': False, 'live': False}
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            shutil.rmtree(directory, ignore_errors=True)
            return {'ok': False, 'error': 'Could not decode microphone audio. Backend needs ffmpeg and ffprobe; use a supported audio format, maximum two hours.'}

    def store(self, rec, source: str) -> dict:
        """Pull completed output over the existing SSH transport. Never remove source."""
        name = source.replace('\\', '/').rsplit('/', 1)[-1]
        if not _NAME.fullmatch(name):
            return {'ok': False, 'error': 'Not a recording filename.'}
        machine = rec.host or socket.gethostname()
        component = quote(machine, safe='@.-_')
        if component in ('.', '..'):
            component = component.replace('.', '%2E')
        directory = self.root / component
        receipt = directory / (name[:-4] + '.library.json')
        with _LOCK:
            entry = None
            try:
                directory.mkdir(parents=True, exist_ok=True)
                entry = json.loads(receipt.read_text()) if receipt.exists() else {
                    'machine': machine, 'host': rec.host, 'source': source,
                    'os_name': rec.os_name, 'recordings_dir': rec.recordings_dir,
                    'file': str(directory / name) if rec.host else source,
                    'completed': [], 'ready': False, 'error': '',
                }
                if entry['ready']:
                    _RETRIES.pop((str(self.root), machine, source), None)
                    return {'ok': True, 'file': entry['file'], 'stored': True}
                if not rec.host:
                    if not Path(source).is_file():
                        raise OSError('Local recording not found.')
                    entry['completed'] = [s for s in _SUFFIXES if Path(source).with_suffix(s).is_file()]
                else:
                    for suffix in _SUFFIXES:
                        if suffix in entry['completed']:
                            continue
                        remote = source[:-4] + suffix
                        if suffix != '.mkv':
                            if rec.os_name == 'windows':
                                from capture_windows_wasapi import _q
                                check = f'if (!(Test-Path -LiteralPath {_q(remote)})) {{ exit 3 }}'
                            else:
                                check = 'test -f ' + shlex.quote(remote)
                            code, detail = rec.run(check)
                            if code in (1, 3):
                                continue
                            if code != 0:
                                raise OSError(detail.strip() or 'Could not check recording sidecars.')
                        target = Path(entry['file']).with_suffix(suffix)
                        staging = target.with_suffix(target.suffix + '.part')
                        if rec.os_name == 'windows':
                            from capture_windows_wasapi import _q
                            command = (f'$b=[IO.File]::ReadAllBytes({_q(remote)}); '
                                       '$o=[Console]::OpenStandardOutput(); $o.Write($b,0,$b.Length)')
                        else:
                            command = 'cat -- ' + shlex.quote(remote)
                        try:
                            with staging.open('wb') as output:
                                done = subprocess.run(rec.shell_argv(command), stdout=output,
                                                      stderr=subprocess.PIPE, timeout=7200)
                            if done.returncode:
                                raise OSError(done.stderr.decode(errors='replace').strip()[:300] or 'Copy failed.')
                            staging.replace(target)
                        finally:
                            staging.unlink(missing_ok=True)
                        entry['completed'].append(suffix)
                        self._save(receipt, entry)
                if rec.host:
                    title = rec.get_title(source)
                    if title.get('ok') and title.get('title'):
                        saved = Recorder(recordings_dir=str(directory)).set_title(entry['file'], title['title'])
                        if not saved.get('ok'):
                            raise OSError(saved.get('error', 'Could not preserve the recording title.'))
                entry.update(ready=True, error='')
                self._save(receipt, entry)
                _ERRORS.pop((str(self.root), machine, source), None)
                _RETRIES.pop((str(self.root), machine, source), None)
                return {'ok': True, 'file': entry['file'], 'stored': True}
            except Exception as exc:
                error = 'Could not store recording on backend: ' + str(exc)[:300]
                _ERRORS[(str(self.root), machine, source)] = error
                _RETRIES[(str(self.root), machine, source)] = rec
                if entry is not None:
                    entry.update(ready=False, error=error)
                    try:
                        self._save(receipt, entry)
                    except OSError:
                        pass
                return {'ok': False, 'stored': False, 'error': error}

    @staticmethod
    def source_recorder(entry):
        return Recorder(host=entry['host'], recordings_dir=entry['recordings_dir'],
                        os_name=entry.get('os_name', 'linux'))

    def recordings(self, selected, limit=20):
        entries = list(self._entries())
        rows = {}
        for _, entry in entries:
            if not entry['ready']:
                continue
            path = Path(entry['file'])
            if not path.is_file():
                continue
            local = Recorder(recordings_dir=str(path.parent))
            title = local.get_title(str(path)).get('title', '')
            transcript = path.with_suffix('.transcript.md')
            rows[(entry['machine'], entry['source'])] = {
                'file': str(path), 'name': path.name, 'machine': entry['machine'],
                'stored': True, 'title': title, 'bytes': path.stat().st_size,
                'duration': local.duration(str(path)),
                'transcript': str(transcript) if transcript.is_file() else None,
                'upload_error': '',
                'origin': entry.get('origin', entry['machine']),
                'capture_mode': entry.get('capture_mode', 'computer'),
                'tracks': entry.get('tracks', ['system', 'microphone']),
                'speaker_separated': entry.get('speaker_separated', True),
            }
        machine = selected.host or socket.gethostname()
        errors = {(e['machine'], e['source']): e.get('error', '') for _, e in entries}
        errors.update({(m, s): error for (root, m, s), error in list(_ERRORS.items()) if root == str(self.root)})
        try:
            for row in selected.recordings(limit):
                key = (machine, row['file'])
                if key not in rows:
                    rows[key] = {**row, 'machine': machine, 'stored': not bool(selected.host),
                                 'upload_error': errors.get(key, '')}
        except Exception:
            # Stored history remains usable when the selected machine is offline.
            pass
        return sorted(rows.values(), key=lambda r: (r['name'], r['machine']), reverse=True)[:max(0, limit)]

    @staticmethod
    def _launch_retry(worker):
        threading.Thread(target=worker, name='call-recorder-upload', daemon=True).start()

    def schedule_retry(self):
        """Do not make history wait for a large transfer or an unreachable source."""
        pending = any(not entry['ready'] for _, entry in self._entries()) or any(
            root == str(self.root) for root, _, _ in list(_RETRIES))
        if not pending or not _RETRY_GATE.acquire(blocking=False):
            return
        def worker():
            try:
                self.retry()
            finally:
                _RETRY_GATE.release()
        try:
            self._launch_retry(worker)
        except Exception:
            _RETRY_GATE.release()
            raise

    def retry(self):
        with _LOCK:
            pending = {(machine, source): rec for (root, machine, source), rec in _RETRIES.items()
                       if root == str(self.root)}
            for _, entry in self._entries():
                if not entry['ready']:
                    pending.setdefault((entry['machine'], entry['source']), self.source_recorder(entry))
            for (_, source), rec in pending.items():
                self.store(rec, source)

    def target(self, path, machine=''):
        """Resolve stored actions from provenance, independent of current host setting."""
        for receipt, entry in self._entries():
            file = entry['file']
            if entry['ready'] and (not machine or machine == entry['machine']) and path in (file, file[:-4] + '.transcript.md'):
                return Recorder(recordings_dir=str(Path(file).parent)), receipt, entry
        return None

    def delete(self, path, machine=''):
        with _LOCK:
            target = self.target(path, machine)
            if not target:
                for receipt, entry in self._entries():
                    if entry['source'] == path and (not machine or entry['machine'] == machine):
                        result = self.source_recorder(entry).delete_recording(path)
                        if result.get('ok'):
                            if entry['host']:
                                for suffix in _SUFFIXES:
                                    Path(entry['file']).with_suffix(suffix).unlink(missing_ok=True)
                            receipt.unlink(missing_ok=True)
                            _ERRORS.pop((str(self.root), entry['machine'], path), None)
                            _RETRIES.pop((str(self.root), entry['machine'], path), None)
                        return result
                return None
            local, receipt, entry = target
            # Keep the stored copy when source deletion fails, so deletion is retryable.
            if entry['host'] and entry['source'] != entry['file']:
                result = self.source_recorder(entry).delete_recording(entry['source'])
                if not result.get('ok') and 'not found' not in result.get('error', '').lower():
                    return result
            result = local.delete_recording(path)
            if result.get('ok'):
                receipt.unlink(missing_ok=True)
            return result
