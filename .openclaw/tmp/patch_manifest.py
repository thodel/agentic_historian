with open('/home/dh/hbls_mcp/server.py', 'r') as f:
    content = f.read()

old = '{"name": "search_persons", "description": "Search persons by given name.'
new = '{"name": "search_bio", "description": "Search persons in bio articles only (family name + forename).",'

if old not in content:
    print('ERROR: old not found')
elif new in content:
    print('search_bio already in manifest')
else:
    content = content.replace(old, new, 1)
    with open('/home/dh/hbls_mcp/server.py', 'w') as f:
        f.write(content)
    print('manifest updated OK')