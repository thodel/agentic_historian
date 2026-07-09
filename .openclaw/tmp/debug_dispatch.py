import asyncio, sys
sys.path.insert(0, '/app')

from starlette.routing import Route as StarletteRoute

original_handle = StarletteRoute.handle

async def patched_handle(self, scope, receive, send):
    print(f'[DEBUG] route.handle called for {self.path}')
    print(f'[DEBUG] self.app = {self.app}')
    print(f'[DEBUG] asyncio.iscoroutinefunction(self.app) = {asyncio.iscoroutinefunction(self.app)}')
    response = self.app(scope, receive, send)
    print(f'[DEBUG] response after calling self.app: {type(response).__name__} = {response}')
    if asyncio.iscoroutine(response):
        response = await response
        print(f'[DEBUG] response after await: {type(response).__name__} = {response}')
    if response is None:
        print('[DEBUG] FATAL: response is None!')
    return await original_handle(self, scope, receive, send)

StarletteRoute.handle = patched_handle

from server import app

from starlette.testclient import TestClient
with TestClient(app, raise_server_exceptions=False) as client:
    r = client.post('/messages/?session_id=abc', json={'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}})
    print(f'Response: {r.status_code} — {r.text[:200]}')