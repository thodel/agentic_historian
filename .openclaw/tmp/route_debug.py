import asyncio, sys
sys.path.insert(0, '/app')
from starlette.routing import Router, Route, Mount
from starlette.testclient import TestClient

# Patch Route.matches to log
original_matches = Route.matches
def patched_matches(self, scope):
    path = scope.get('path', '?')
    method = scope.get('method', '?')
    print(f'[Route.matches] path={path} method={method} route_path={self.path}', flush=True)
    result = original_matches(self, scope)
    print(f'[Route.matches] -> {result[0]}', flush=True)
    return result
Route.matches = patched_matches

# Patch Mount.matches too
original_mount_matches = Mount.matches
def patched_mount_matches(self, scope):
    path = scope.get('path', '?')
    method = scope.get('method', '?')
    print(f'[Mount.matches] path={path} method={method} mount_path={self.path}', flush=True)
    result = original_mount_matches(self, scope)
    print(f'[Mount.matches] -> {result[0]}', flush=True)
    return result
Mount.matches = patched_mount_matches

from server import app

with TestClient(app, raise_server_exceptions=False) as client:
    print('--- POST /messages/?session_id=abc ---', flush=True)
    r = client.post('/messages/?session_id=abc', json={'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}})
    print(f'Response: {r.status_code}', flush=True)