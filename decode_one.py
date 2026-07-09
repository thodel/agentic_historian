#!/usr/bin/env python3
"""Decode a single web_fetch response text field back to binary."""
import sys, os, re, json

if len(sys.argv) < 3:
    print("Usage: decode_one.py <text> <raw_length>")
    sys.exit(1)

text = sys.argv[1]
raw_length = int(sys.argv[2])

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
    sec_end = text.find('\n\n\n', sec_idx)
    if sec_end >= 0:
        text = text[sec_end + 3:]

# Now text contains bytes encoded as unicode escapes and raw chars
# raw_unicode_escape: each \uXXXX becomes the unicode char, then latin-1 gives byte
encoded = text.encode('raw_unicode_escape')
decoded = encoded.decode('latin-1')
byte_data = decoded.encode('latin-1')
result = byte_data[:raw_length]
sys.stdout.buffer.write(result)
