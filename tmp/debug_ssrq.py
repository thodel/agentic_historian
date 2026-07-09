import re
TTL = '/home/dh/resources/ssrq__fuseki_042810.ttl'
OUTER_RE = re.compile(r'^\s*<(http://ssrq-sds-fds\.ch/Register/#(per|org|loc)(\d+)>$')

with open(TTL, 'r', encoding='utf-8', errors='replace') as f:
    for i, line in enumerate(f):
        m = OUTER_RE.match(line)
        if m and m.group(2) == 'per' and m.group(3) == '000001':
            print(f'per000001 starts at line {i+1}')
            lines = [next(f) for _ in range(40)]
            for j, l in enumerate(lines):
                print(f'{j:3d}: {repr(l[:120])}')
            break