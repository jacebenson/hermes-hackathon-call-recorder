"""Single microphone mix transcription, isolated from the native two-track pass."""
from pathlib import Path
import os
import subprocess


def transcribe_microphone(path: str, runtime: dict) -> dict:
    python = runtime.get('python')
    if not runtime.get('ok') or not python:
        return {'ok': False, 'engine': 'none',
                'error': 'Backend Whisper unavailable. Audio is saved; install faster-whisper on the backend to transcribe.'}
    env = {**os.environ, 'CALLREC_SINGLE_TRACK': '1',
           'WHISPER_MODEL': runtime.get('model') or 'tiny.en',
           'WHISPER_DEVICE': 'cpu', 'WHISPER_COMPUTE': 'int8'}
    try:
        result = subprocess.run([python, str(Path(__file__).parent / 'scripts' / 'archive.py'), path],
                                env=env, capture_output=True, timeout=7200)
        output = Path(path).with_suffix('.transcript.md')
        if result.returncode or not output.is_file():
            raise RuntimeError('Transcription failed')
        return {'ok': True, 'engine': 'whisper', 'path': str(output),
                'speaker_separated': False}
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return {'ok': False, 'engine': 'whisper', 'error': 'Backend microphone transcription failed. Audio remains saved.'}
