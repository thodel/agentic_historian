#!/usr/bin/env python3
"""Process responses from web_fetch and decode to image files."""
import json, os, sys, re

WORKDIR = "/home/dh/.openclaw/tmp/dmj_vol14"
os.makedirs(WORKDIR, exist_ok=True)

def decode_bytes(text, raw_length):
    """Decode web_fetch text field back to raw bytes."""
    # Find end marker
    end_idx = text.find('<<<END_EXTERNAL_UNTRUSTED_CONTENT')
    if end_idx >= 0:
        text = text[:end_idx]
    
    # Find start marker
    start_idx = text.find('>>>\n')
    if start_idx >= 0:
        text = text[start_idx + 4:]
    else:
        start_idx = text.find('>>>')
        if start_idx >= 0:
            text = text[start_idx + 3:].lstrip()
    
    # Remove security notice
    sec_idx = text.find('SECURITY NOTICE')
    if sec_idx >= 0:
        sec_end = text.find('\n\n\n', sec_idx)
        if sec_end >= 0:
            text = text[sec_end + 3:]
    
    # Decode: encode as raw_unicode_escape, decode as latin-1, take rawLength bytes
    encoded = text.encode('raw_unicode_escape')
    decoded = encoded.decode('latin-1')
    byte_data = decoded.encode('latin-1')
    return byte_data[:raw_length]

def convert_url(url):
    """Convert Wayback URL to use 2024id_ for latest version."""
    return re.sub(r'/web/\d+', '/web/2024id_', url)

# 11 missing files
missing = [
    {
        "fname": "Bitner_-_A_Macron_Signifying_Nothing_img04_dm-14-1-8068-g3.png",
        "url": "https://web.archive.org/web/2024id_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g3.png",
        "path": "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img04_dm-14-1-8068-g3.png"
    },
    {
        "fname": "Bitner_-_A_Macron_Signifying_Nothing_img05_dm-14-1-8068-g2.png",
        "url": "https://web.archive.org/web/2024id_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g2.png",
        "path": "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img05_dm-14-1-8068-g2.png"
    },
    {
        "fname": "Bitner_-_A_Macron_Signifying_Nothing_img06_dm-14-1-8068-g15.png",
        "url": "https://web.archive.org/web/2024id_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g15.png",
        "path": "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img06_dm-14-1-8068-g15.png"
    },
    {
        "fname": "Bitner_-_A_Macron_Signifying_Nothing_img07_dm-14-1-8068-g12.png",
        "url": "https://web.archive.org/web/2024id_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g12.png",
        "path": "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img07_dm-14-1-8068-g12.png"
    },
    {
        "fname": "Bitner_-_A_Macron_Signifying_Nothing_img08_",
        "url": "https://web.archive.org/web/2024id_/https://journal.digitalmedievalist.org/article/id/8068/file/113418/",
        "path": "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img08_"
    },
    {
        "fname": "Bitner_-_A_Macron_Signifying_Nothing_img09_dm-14-1-8068-g7.png",
        "url": "https://web.archive.org/web/2024id_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g7.png",
        "path": "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img09_dm-14-1-8068-g7.png"
    },
    {
        "fname": "Bitner_-_A_Macron_Signifying_Nothing_img11_dm-14-1-92-g13.png",
        "url": "https://web.archive.org/web/2024id_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-92-g13.png",
        "path": "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img11_dm-14-1-92-g13.png"
    },
    {
        "fname": "Bitner_-_A_Macron_Signifying_Nothing_img19_dm-14-1-8068-g5.png",
        "url": "https://web.archive.org/web/2024id_/https://journal.digitalmedievalist.org/article/id/8068/dm-14-1-8068-g5.png",
        "path": "/home/dh/.openclaw/tmp/dmj_vol14/Bitner_-_A_Macron_Signifying_Nothing_img19_dm-14-1-8068-g5.png"
    },
    {
        "fname": "Dase_-_Pacience_is_an_Heigh_Vertu_img00_dm-14-1-8069-g2.png",
        "url": "https://web.archive.org/web/2024id_/https://journal.digitalmedievalist.org/article/id/8069/dm-14-1-8069-g2.png",
        "path": "/home/dh/.openclaw/tmp/dmj_vol14/Dase_-_Pacience_is_an_Heigh_Vertu_img00_dm-14-1-8069-g2.png"
    },
    {
        "fname": "Bordalejo_-_Canterbury_Tales_Project_Special_Issue_Introduction_img00_",
        "url": "https://web.archive.org/web/2024id_/https://journal.digitalmedievalist.org/article/id/8072/file/113412/",
        "path": "/home/dh/.openclaw/tmp/dmj_vol14/Bordalejo_-_Canterbury_Tales_Project_Special_Issue_Introduction_img00_"
    },
    {
        "fname": "Bordalejo_-_Canterbury_Tales_Project_Special_Issue_Introduction_img01_0beed22c-ffa8-44b5-858c-55986e1a3847.png",
        "url": "https://web.archive.org/web/2024id_/https://journal.digitalmedievalist.org/media/cover_images/0beed22c-ffa8-44b5-858c-55986e1a3847.png",
        "path": "/home/dh/.openclaw/tmp/dmj_vol14/Bordalejo_-_Canterbury_Tales_Project_Special_Issue_Introduction_img01_0beed22c-ffa8-44b5-858c-55986e1a3847.png"
    },
]

# Read responses from stdin (passed as JSON)
# The subagent will write the web_fetch responses to a temp file
response_file = sys.argv[1] if len(sys.argv) > 1 else None

if response_file and os.path.exists(response_file):
    with open(response_file) as f:
        responses = json.load(f)
    
    print(f"Processing {len(responses)} responses...")
    succeeded = 0
    failed = 0
    
    for resp in responses:
        fname = resp.get('fname', 'unknown')
        text = resp.get('text', '')
        raw_length = resp.get('rawLength', 0)
        
        # Find matching task
        task = next((t for t in missing if t['fname'] == fname), None)
        if not task:
            print(f"  {fname}: no matching task")
            failed += 1
            continue
        
        path = task['path']
        
        if not text:
            print(f"  {fname}: empty text, status={resp.get('status')}")
            failed += 1
            continue
        
        if raw_length == 0:
            print(f"  {fname}: rawLength is 0")
            failed += 1
            continue
        
        try:
            byte_data = decode_bytes(text, raw_length)
            if byte_data is None or len(byte_data) == 0:
                print(f"  {fname}: decode returned empty")
                failed += 1
                continue
            
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(byte_data)
            
            size = os.path.getsize(path)
            print(f"  {fname}: OK ({size} bytes)")
            succeeded += 1
        except Exception as e:
            print(f"  {fname}: error - {e}")
            failed += 1
    
    print(f"\n=== Result: {succeeded} succeeded, {failed} failed ===")
else:
    # Show expected responses format
    print("No response file provided. Expected responses in JSON format.")
    print(f"Missing files: {len(missing)}")
    for t in missing:
        print(f"  {t['fname']}")