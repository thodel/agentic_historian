import sys, os
sys.stdout = open('/tmp/out.txt', 'w')
sys.stderr = open('/tmp/err.txt', 'w')
sys.path.insert(0, '/app')
from starlette.routing import Route, Mount, Match
import starlette.routing as routing

original_get_route_path = routing.get_route_path
def patched_get_route_path(scope, current_route=None):
    path = original_get_route_path(scope, current_route)
    print(f'[get_route_path] scope_path={scope.get("path")} current_route={current_route} -> {path}', flush=True)
    return path
routing.get_route_path = patched_get_route_path

original_matches = Route.matches
def patched_matches(self, scope):
    print(f'[Route.matches] self.path={self.path} self.methods={self.methods} scope_path={scope.get("path")} method={scope.get("method")}', flush=True)
    result = original_matches(self, scope)
    print(f'[Route.matches] -> {result[0]}', flush=True)
    return result
Route.matches = patched_matches

from server import app
from starlette.testclient import TestClient

with TestClient(app, raise_server_exceptions=False) as client:
    print('--- POST /messages/?session_id=abc ---', flush=True)
    r = client.post('/messages/?session_id=abc', json={'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}})
    print(f'Response: {r.status_code}', flush=True)