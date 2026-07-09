import sys
sys.stdout = open('/tmp/out.txt', 'w')
sys.stderr = open('/tmp/err.txt', 'w')
sys.path.insert(0, '/app')
sys.path.insert(0, '/usr/local/lib/python3.12/site-packages')

from starlette.routing import Router, Route, Mount, Match
import starlette.routing as routing

# Trace get_route_path for POST vs GET
original_get_route_path = routing.get_route_path
def patched_get_route_path(scope, current_route=None):
    path = original_get_route_path(scope, current_route)
    return path
routing.get_route_path = patched_get_route_path

# Trace Router.handle method
original_router_handle = Router.handle
async def patched_router_handle(self, scope, receive, send):
    # Get method and path
    method = scope.get('method', '?')
    path = scope.get('path', '?')
    print(f'[Router.handle] method={method} path={path}', flush=True)
    
    # Get matches
    from starlette.routing import get_route_path
    route_path = get_route_path(scope)
    print(f'[Router.handle] route_path={route_path}', flush=True)
    
    for route in self.routes:
        match, child_scope = route.matches(scope)
        print(f'[Router.handle] route={route.path if hasattr(route,"path") else route} match={match}', flush=True)
        if match == Match.FULL:
            endpoint = child_scope.get('endpoint')
            print(f'[Router.handle] FULL match, endpoint={endpoint}, methods={getattr(route,"methods",None)}', flush=True)
            break
        elif match == Match.PARTIAL:
            endpoint = child_scope.get('endpoint')
            methods = getattr(route, 'methods', None)
            print(f'[Router.handle] PARTIAL match, endpoint={endpoint}, route_methods={methods}, scope_method={method}', flush=True)
            if methods and method not in methods:
                print(f'[Router.handle] -> would return 405', flush=True)
            break
    
    return await original_router_handle(self, scope, receive, send)

Router.handle = patched_router_handle

from server import app
from starlette.testclient import TestClient

with TestClient(app, raise_server_exceptions=False) as client:
    print('--- GET /messages/?session_id=abc ---', flush=True)
    r = client.get('/messages/?session_id=abc')
    print(f'GET Response: {r.status_code}', flush=True)
    print('--- POST /messages/?session_id=abc ---', flush=True)
    r = client.post('/messages/?session_id=abc', json={'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}})
    print(f'POST Response: {r.status_code}', flush=True)