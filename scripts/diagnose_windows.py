"""Enumerate audio in the interactive desktop. Never start a recording."""
import json
from pathlib import Path
import sys


def devices():
    import pyaudiowpatch as audio
    with audio.PyAudio() as p:
        api = p.get_host_api_info_by_type(audio.paWASAPI)
        output = p.get_device_info_by_index(api['defaultOutputDevice'])
        microphone = p.get_device_info_by_index(api['defaultInputDevice'])
        loopback = output if output.get('isLoopbackDevice') else next(
            (d for d in p.get_loopback_device_info_generator() if output['name'] in d['name']), None)
        if not loopback or microphone.get('maxInputChannels', 0) < 1 or microphone.get('isLoopbackDevice'):
            return {'ok': False, 'message': 'WASAPI loopback or default microphone is missing.'}
        return {'ok': True, 'message': f"WASAPI loopback {loopback['name']} and microphone {microphone['name']} are present in the logged-in session."}


if __name__ == '__main__':
    try:
        result = devices()
    except Exception as exc:
        result = {'ok': False, 'message': f'Could not find WASAPI loopback and microphone: {exc}'}
    Path(sys.argv[1]).write_text(json.dumps(result), encoding='utf-8')
