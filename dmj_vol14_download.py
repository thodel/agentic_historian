#!/usr/bin/env python3
"""Download images from Wayback Machine URLs - parallel version."""

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
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

def download_one(task):
    """Download one file using curl."""
    url = task['url']
    path = task['path']
    fname = task['fname']
    
    # Convert im_ to if_ for Wayback image URLs
    if '/im_/' in url:
        url = url.replace('/im_/', '/if_/', 1)
    
    cmd = ['curl', '-s', '-L', '-o', path, '-m', '30', '-w', '%{http_code}', url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        http_code = result.stdout.strip()[-3:]
        if http_code == '200':
            size = os.path.getsize(path) if os.path.exists(path) else 0
            if size > 1000:
                return ('success', fname, size)
            else:
                if os.path.exists(path):
                    os.remove(path)
                return ('fail', fname, f"too small ({size} bytes)")
        else:
            if os.path.exists(path):
                os.remove(path)
            return ('fail', fname, f"HTTP {http_code}")
    except subprocess.TimeoutExpired:
        return ('fail', fname, "timeout")
    except Exception as e:
        return ('fail', fname, str(e))

def main():
    tasks = load_tasks()
    total = len(tasks)
    
    to_download = [t for t in tasks if needs_download(t)]
    already_done = [t for t in tasks if not needs_download(t)]
    
    print(f"Total tasks: {total}")
    print(f"Already done: {len(already_done)}")
    print(f"Need to download: {len(to_download)}")
    print()
    
    os.makedirs(OUT_DIR, exist_ok=True)
    
    succeeded = 0
    failed = []
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(download_one, t): t for t in to_download}
        for i, future in enumerate(as_completed(futures)):
            status, fname, info = future.result()
            if status == 'success':
                print(f"[{i+1}/{len(to_download)}] OK: {fname} ({info} bytes)")
                succeeded += 1
            else:
                print(f"[{i+1}/{len(to_download)}] FAIL: {fname} - {info}")
                failed.append((fname, info))
    
    print(f"\n{'='*60}")
    print(f"Summary: {succeeded} succeeded, {len(failed)} failed, {len(already_done)} skipped")
    if failed:
        print("\nFailures:")
        for fname, err in failed:
            print(f"  - {fname}: {err}")
    
    # Final verification
    all_tasks = load_tasks()
    valid = sum(1 for t in all_tasks if os.path.exists(t['path']) and os.path.getsize(t['path']) > 1000)
    print(f"\nFinal: {valid}/{total} files valid (>1000 bytes)")

if __name__ == '__main__':
    main()