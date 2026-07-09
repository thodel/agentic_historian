#!/usr/bin/env python3
with open('/etc/nginx/sites-available/tei.dh.unibe.ch', 'r') as f:
    c = f.read()
b = """
# ── Voyant Tools ────────────────────────────────────
location /voyant {
    return 301 /voyant/;
}
location /voyant/ {
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    sub_filter_once off;
    sub_filter_types text/html application/javascript;
    sub_filter 'href="/' 'href="/voyant/';
    sub_filter "src=\\"/" "src=\\"/voyant/";
    sub_filter "url('/" "url('/voyant/";
    sub_filter 'url("/' 'url("/voyant/';
    proxy_pass http://127.0.0.1:8888/;
}

"""
m = "    location /mcp/kf {"
c = c.replace(m, "\n" + b + m)
with open('/etc/nginx/sites-available/tei.dh.unibe.ch', 'w') as f:
    f.write(c)
print("nginx config updated")