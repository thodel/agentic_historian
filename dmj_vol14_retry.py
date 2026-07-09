#!/usr/bin/env python3
"""Retry failed downloads sequentially."""

import json
import os
import subprocess
import time

TASKS_FILE = '/home/dh/.openclaw/tmp/dmj_vol14_tasks.json'
OUT_DIR = '/home/dh/.openclaw/tmp/dmj_vol14/'

FAILED = [
    ("Bitner_-_A_Macron_Signifying_Nothing_img05_dm-14-1-8068-g2.png", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g2.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img05_dm-14-1-8068-g2.png"),
    ("Bitner_-_A_Macron_Signifying_Nothing_img01_dm-14-1-8068-g16.png", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g16.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img01_dm-14-1-8068-g16.png"),
    ("Bitner_-_A_Macron_Signifying_Nothing_img04_dm-14-1-8068-g3.png", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g3.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img04_dm-14-1-8068-g3.png"),
    ("Bitner_-_A_Macron_Signifying_Nothing_img03_dm-14-1-8068-g8.png", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g8.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img03_dm-14-1-8068-g8.png"),
    ("Bitner_-_A_Macron_Signifying_Nothing_img08_", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/file/113418/", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img08_"),
    ("Bitner_-_A_Macron_Signifying_Nothing_img07_dm-14-1-8068-g12.png", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g12.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img07_dm-14-1-8068-g12.png"),
    ("Bitner_-_A_Macron_Signifying_Nothing_img06_dm-14-1-8068-g15.png", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g15.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img06_dm-14-1-8068-g15.png"),
    ("Bitner_-_A_Macron_Signifying_Nothing_img09_dm-14-1-8068-g7.png", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g7.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img09_dm-14-1-8068-g7.png"),
    ("Dase_-_Pacience_is_an_Heigh_Vertu_img00_dm-14-1-8069-g2.png", "https://web.archive.org/web/20220702192817im_/https://journal.digitalmedievalist.org/article/id/8069/dm-14-1-8069-g2.png", "/home/dh/.openclaw/tmp/dmj_vol14/Dase_-_Pacience_is_an_Heigh_Vertu_img00_dm-14-1-8069-g2.png"),
    ("Bitner_-_A_Macron_Signifying_Nothing_img11_dm-14-1-92-g13.png", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-92-g13.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img11_dm-14-1-92-g13.png"),
    ("Bitner_-_A_Macron_Signifying_Nothing_img19_dm-14-1-8068-g5.png", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g5.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img19_dm-14-1-8068-g5.png"),
    ("Bordalejo_-_Canterbury_Tales_Project_Special_Issue_Introduction_img00_", "https://web.archive.org/web/20220702192814im_/https://journal.digitalmedievalist.org/article/id/8064/file/113403/", "/home/dh/.openclaw/tmp/dmj_vol14/Bordalejo_-_Canterbury_Tales_Project_Special_Issue_Introduction_img00_"),
    ("Bordalejo_-_Canterbury_Tales_Project_Special_Issue_Introduction_img01_0beed22c-ffa8-44b5-858c-55986e1a3847.png", "https://web.archive.org/web/20220702192814im_/https://journal.digitalmedievalist.org/media/cover_images/0beed22c-ffa8-44b5-858c-55986e1a3847.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bordalejo_-_Canterbury_Tales_Project_Special_Issue_Introduction_img01_0beed22c-ffa8-44b5-858c-55986e1a3847.png"),
]

# Also check for 4 files that are suspiciously small (~1200 bytes)
SMALL_FILES = [
    ("Bitner_-_A_Macron_Signifying_Nothing_img12_dm-14-1-8068-g18.png", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g18.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img12_dm-14-1-8068-g18.png"),
    ("Bitner_-_A_Macron_Signifying_Nothing_img14_dm-14-1-8068-g17.png", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g17.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img14_dm-14-1-8068-g17.png"),
    ("Bitner_-_A_Macron_Signifying_Nothing_img15_dm-14-1-92-g14.png", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-92-g14.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img15_dm-14-1-92-g14.png"),
    ("Bitner_-_A_Macron_Signifying_Nothing_img18_dm-14-1-8068-g11.png", "https://web.archive.org/web/20220702192815im_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g11.png", "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img18_dm-14-1-8068-g11.png"),
]

def download_with_curl(url, path, delay=2.0):
    """Download with delay between requests to avoid rate limiting."""
    time.sleep(delay)
    url_fixed = url.replace('/im_/', '/if_/', 1)
    cmd = ['curl', '-s', '-L', '-o', path, '-m', '60', '-w', '%{http_code}', url_fixed]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=70)
        http_code = result.stdout.strip()[-3:]
        if http_code == '200':
            size = os.path.getsize(path) if os.path.exists(path) else 0
            return http_code, size
        return http_code, 0
    except Exception as e:
        return str(e), 0

def retry_list(items, label):
    print(f"\n=== Retrying {label} ({len(items)} files) ===")
    succeeded = 0
    failed = []
    for i, (fname, url, path) in enumerate(items):
        print(f"[{i+1}/{len(items)}] {fname}...", end=" ", flush=True)
        http_code, size = download_with_curl(url, path)
        if http_code == '200' and size > 1000:
            print(f"OK ({size} bytes)")
            succeeded += 1
        else:
            if os.path.exists(path) and os.path.getsize(path) <= 1000:
                os.remove(path)
            print(f"FAIL (HTTP {http_code}, size={size})")
            failed.append((fname, f"HTTP {http_code}, size={size}"))
    print(f"  -> {succeeded} succeeded, {len(failed)} failed")
    return succeeded, failed

def main():
    s1, f1 = retry_list(FAILED, "failed downloads")
    s2, f2 = retry_list(SMALL_FILES, "small files (<1200 bytes)")
    
    total_failures = f1 + f2
    print(f"\n{'='*60}")
    print(f"Retry results: {s1 + s2} succeeded, {len(total_failures)} still failing")
    
    if total_failures:
        print("\nRemaining failures:")
        for fname, err in total_failures:
            print(f"  - {fname}: {err}")

if __name__ == '__main__':
    main()