#!/usr/bin/env python3
"""Decode web_fetch JSON output and save binary image to path."""
import json
import sys
import os
import re

def decode_bytes(text, raw_length):
    """Convert web_fetch text field back to bytes."""
    # Find end of security wrapper content
    end_marker = '<<<END_EXTERNAL_UNTRUSTED_CONTENT'
    idx = text.find(end_marker)
    if idx >= 0:
        text = text[:idx]
    
    # Find start marker
    start_idx = text.find('>>>\n')
    if start_idx >= 0:
        text = text[start_idx + 4:]
    else:
        # fallback - try just >>>
        start_idx = text.find('>>>')
        if start_idx >= 0:
            text = text[start_idx + 3:].lstrip()

    # Encode as raw_unicode_escape (handles \uXXXX sequences)
    # Then decode as latin-1 to get original byte values
    encoded = text.encode('raw_unicode_escape')
    decoded = encoded.decode('latin-1')
    byte_data = decoded.encode('latin-1')
    return byte_data[:raw_length]

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: save_fetched.py <output_path> <response_json_file>", file=sys.stderr)
        sys.exit(1)
    
    out_path = sys.argv[1]
    json_file = sys.argv[2]
    
    with open(json_file) as f:
        resp = json.load(f)
    
    if resp.get("status") != "success":
        print(f"ERROR: status={resp.get('status')}", file=sys.stderr)
        sys.exit(1)
    
    data = resp.get("data", {})
    raw_length = data.get("rawLength", 0)
    text = data.get("text", "")
    truncated = data.get("truncated", False)
    
    if truncated:
        print(f"WARNING: response truncated at {raw_length} bytes", file=sys.stderr)
    
    byte_data = decode_bytes(text, raw_length)
    
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'wb') as f:
        f.write(byte_data)
    
    size = os.path.getsize(out_path)
    print(f"Saved {size} bytes to {out_path}")