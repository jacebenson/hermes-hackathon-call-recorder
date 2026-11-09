"""Disposable app contract. Production mounts this same router behind Hermes auth."""
import io
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from unittest.mock import patch
from dashboard import plugin_api as api
from recording_library import RecordingLibrary

app = FastAPI()
@app.middleware('http')
async def test_session(request: Request, call_next):
    if request.headers.get('X-Hermes-Session-Token') != 'disposable-test-session':
        return JSONResponse({'detail':'Unauthorized'}, status_code=401)
    return await call_next(request)
app.include_router(api.router, prefix='/api/plugins/call-recorder')
client = TestClient(app)
url = '/api/plugins/call-recorder/browser-audio'
assert client.post(url, files={'file':('x',b'audio','audio/webm')}).status_code == 401
headers = {'X-Hermes-Session-Token':'disposable-test-session'}
assert not client.post(url, headers=headers).json()['ok']
assert not client.post(url, headers=headers, files={'file':('x',b'bad','audio/webm')}).json()['ok']
assert not client.post(url, headers={**headers, 'Content-Length':str(40*1024*1024)}, content=b'x').json()['ok']
# Unknown extra fields, multiple files, empty, video MIME and untrusted filename.
assert not client.post(url, headers=headers, files={'file':('x',b'','audio/webm')}).json()['ok']
assert not client.post(url, headers=headers, files={'file':('x',b'abc','video/webm')}).json()['ok']
assert not client.post(url, headers=headers, files=[('file',('x',b'a','audio/webm')),('file',('y',b'b','audio/webm'))]).json()['ok']
assert not client.post(url, headers=headers, files={'file':('x',b'a','audio/webm')}, data={'path':'/outside'}).json()['ok']
data = subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=0.3',
                       '-c:a','libopus','-f','webm','pipe:1'], capture_output=True, check=True).stdout
with patch.object(api, 'detect_transcription', return_value={'ok':False}):
    result = client.post(url, headers=headers, files={'file':('../../outside.webm',data,'audio/webm')}).json()
assert result['ok'], result
assert Path(result['file']).is_relative_to(RecordingLibrary().root)
assert Path(result['file']).name.startswith('call-')
assert result['transcription']['state'] == 'unavailable'
assert RecordingLibrary().target(result['file'])[2]['tracks'] == ['microphone']
print('Multipart auth, bounds, invalid media, safe filename and persistence verified.')
