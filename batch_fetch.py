#!/usr/bin/env python3
"""Batch fetch images via web_fetch CLI (tool call interface) and decode to files."""
import json, os, subprocess, re, sys

WORKDIR = "/home/dh/.openclaw/tmp/dmj_vol14"
OPENCLAW = os.path.expanduser("~/.npm-global/bin/openclaw")

# Load tasks
with open("/home/dh/.openclaw/tmp/dmj_vol14_tasks.json") as f:
    tasks = json.load(f)

# Filter to missing
missing = []
for t in tasks:
    path = t["path"]
    if not os.path.exists(path) or os.path.getsize(path) <= 1000:
        missing.append(t)

print(f"Processing {len(missing)} missing files...")

def convert_url_to_wayback(url):
    """Replace timestamp in Wayback URL with 2024id_ for reliable access."""
    # Pattern: /web/TIMESTAMPim_/ or /web/TIMESTAMP_/
    return re.sub(r'/web/\d+', '/web/2024id_', url)

def decode_response_text(text, raw_length):
    """Decode web_fetch text field back to raw bytes."""
    # Find actual content boundaries
    end_idx = text.find('<<<END_EXTERNAL_UNTRUSTED_CONTENT')
    if end_idx >= 0:
        text = text[:end_idx]
    
    start_idx = text.find('>>>\n')
    if start_idx >= 0:
        text = text[start_idx + 4:]
    else:
        start_idx = text.find('>>>')
        if start_idx >= 0:
            text = text[start_idx + 3:].lstrip()
    
    # Remove security notice at the beginning if present
    sec_idx = text.find('SECURITY NOTICE')
    if sec_idx >= 0:
        # Find the end of security notice
        sec_end = text.find('\n\n\n', sec_idx)
        if sec_end >= 0:
            text = text[sec_end + 3:]
    
    # Now text contains bytes encoded as unicode escapes and raw chars
    # raw_unicode_escape: each \uXXXX becomes the unicode char, then latin-1 gives byte
    try:
        encoded = text.encode('raw_unicode_escape')
        decoded = encoded.decode('latin-1')
        byte_data = decoded.encode('latin-1')
        return byte_data[:raw_length]
    except Exception as e:
        print(f"    Decode error: {e}")
        return None

def fetch_and_save(task):
    """Call openclaw tool call web_fetch for a single URL, save response, decode."""
    url = task["url"]
    path = task["path"]
    fname = task["fname"]
    
    # Try the Wayback "latest" URL format
    wayback_url = convert_url_to_wayback(url)
    
    # Build the openclaw tool call command
    # Use web_search endpoint structure or openclaw tool call
    args = json.dumps({
        "url": wayback_url,
        "extractMode": "text",
        "maxChars": 200000
    })
    
    # Try using openclaw tool call via the gateway or direct
    # First try: openclaw tool call
    cmd = [OPENCLAW, "tool", "call", "web_fetch", "--json", args]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and result.stdout.strip():
            try:
                resp = json.loads(result.stdout)
            except:
                return False, f"Could not parse JSON: {result.stdout[:200]}"
        else:
            return False, f"openclaw tool call failed: {result.stderr[:200]}"
    except FileNotFoundError:
        return False, "openclaw not found"
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)
    
    if resp.get("status") != "success":
        return False, f"status={resp.get('status')}"
    
    data = resp.get("data", {})
    raw_length = data.get("rawLength", 0)
    text = data.get("text", "")
    truncated = data.get("truncated", False)
    
    if truncated:
        print(f"    Warning: truncated at {raw_length} bytes")
    
    byte_data = decode_response_text(text, raw_length)
    if byte_data is None:
        return False, "Decode returned None"
    
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(byte_data)
    
    size = os.path.getsize(path)
    return True, f"OK ({size} bytes, raw={raw_length})"

# Process all missing
succeeded = []
failed = []

for i, task in enumerate(missing):
    fname = task["fname"]
    print(f"[{i+1}/{len(missing)}] {fname}...", end=" ", flush=True)
    ok, msg = fetch_and_save(task)
    print(msg)
    if ok:
        succeeded.append(task)
    else:
        failed.append(task)

print(f"\n=== FINAL: {len(succeeded)}/{len(missing)} succeeded ===")
if failed:
    print(f"Failed ({len(failed)}):")
    for t in failed:
        print(f"  {t['fname']}: {t['url']}")
        print(f"    Path: {t['path']}")
