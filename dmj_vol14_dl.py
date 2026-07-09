#!/usr/bin/env python3
"""Download images from Wayback Machine URLs - wrapper script."""

import json
import os
import re
import subprocess
import sys

TASKS_FILE = '/home/dh/.openclaw/tmp/dmj_vol14_tasks.json'
OUT_DIR = '/home/dh/.openclaw/tmp/dmj_vol14/'

def load_tasks():
    with open(TASKS_FILE) as f:
        return json.load(f)

def needs_download(task):
    """Check if file needs downloading (doesn't exist or too small)."""
    p = task['path']
    if not os.path.exists(p):
        return True
    if os.path.getsize(p) <= 1000:
        return True
    return False

def call_web_fetch(url):
    """Call web_fetch tool via openclaw CLI."""
    cmd = [
        'openclaw', 'tools', 'call', 'web_fetch',
        '--url', url,
        '--extractMode', 'text'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout + result.stderr

def decode_raw_bytes_to_file(text, path):
    """Decode \\xNN escape sequences and write binary file."""
    # Find all \xNN sequences and rebuild as bytes
    bytes_data = bytearray()
    i = 0
    while i < len(text):
        if i < len(text) - 3 and text[i:i+2] == '\\x':
            hex_pair = text[i+2:i+4]
            try:
                bytes_data.append(int(hex_pair, 16))
                i += 4
            except ValueError:
                bytes_data.append(ord(text[i]))
                i += 1
        else:
            bytes_data.append(ord(text[i]) if isinstance(text[i], str) else text[i])
            i += 1
    
    with open(path, 'wb') as f:
        f.write(bytes_data)
    return len(bytes_data)

def main():
    tasks = load_tasks()
    total = len(tasks)
    
    to_download = [t for t in tasks if needs_download(t)]
    already_done = [t for t in tasks if not needs_download(t)]
    
    print(f"Total tasks: {total}")
    print(f"Already done (>1000 bytes): {len(already_done)}")
    print(f"Need to download: {len(to_download)}")
    print()
    
    os.makedirs(OUT_DIR, exist_ok=True)
    
    succeeded = 0
    failed = []
    skipped_existing = len(already_done)
    
    for i, task in enumerate(to_download):
        url = task['url']
        path = task['path']
        fname = task['fname']
        
        print(f"[{i+1}/{len(to_download)}] Fetching: {fname}...", end=" ", flush=True)
        
        # Call web_fetch
        result = call_web_fetch(url)
        
        # Parse JSON response
        try:
            # Find JSON in output
            json_start = result.find('{')
            if json_start == -1:
                print(f"FAILED - no JSON found")
                failed.append((fname, "No JSON response"))
                continue
            
            json_str = result[json_start:]
            resp = json.loads(json_str)
            
            if resp.get('status') == 'error':
                print(f"FAILED - {resp.get('error', 'unknown error')}")
                failed.append((fname, resp.get('error', 'unknown')))
                continue
            
            content = resp.get('text', '')
            raw_length = resp.get('rawLength', len(content))
            
            if not content:
                print(f"FAILED - empty content")
                failed.append((fname, "Empty content"))
                continue
            
            # Decode escape sequences and write
            size = decode_raw_bytes_to_file(content, path)
            print(f"OK ({size} bytes)")
            succeeded += 1
            
        except json.JSONDecodeError as e:
            print(f"FAILED - JSON parse error: {e}")
            failed.append((fname, f"JSON parse error: {e}"))
            continue
        except Exception as e:
            print(f"FAILED - {e}")
            failed.append((fname, str(e)))
            continue
    
    print(f"\n{'='*50}")
    print(f"Summary: {succeeded} succeeded, {len(failed)} failed, {skipped_existing} skipped (already existed)")
    if failed:
        print("\nFailures:")
        for fname, err in failed:
            print(f"  - {fname}: {err}")

if __name__ == '__main__':
    main()