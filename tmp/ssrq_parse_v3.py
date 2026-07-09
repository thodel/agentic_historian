#!/usr/bin/env python3
import re, sqlite3, json, os

TTL = '/home/dh/resources/ssrq__fuseki_042810.ttl'
DB  = '/data/ssrq.db'

OUTER_RE  = re.compile(r'^\s*<(http://ssrq-sds-fds\.ch/Register/#(per|org|loc)(\d+)>')
TRIPLE_RE = re.compile(r'^\s*(\w+):(\w+)\s+"([^"]*)"(?:\s*@(\w+))?\s*\.?\s*$')
URI_ID    = re.compile(r'^\s*(\w+):(\w+)\s+<[^#]*#(per|org|loc)(\w+)>')
YEAR_RE   = re.compile(r'\b(1[3-9]\d{2}|20[0-2]\d)\b')
FORENAME  = re.compile(r'pers:forename\s+"([^"]*)"')
SURNAME   = re.compile(r'pers:surname\s+"([^"]*)"')
NAMETYPE  = re.compile(r'pers:type\s+"([^"]*)"')
ORG_TYPE  = re.compile(r'pers:org_type\s+"([^"]*)"')

def init_db(conn):
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('''CREATE TABLE IF NOT EXISTS persons (
        id TEXT PRIMARY KEY, uri TEXT, etype TEXT DEFAULT 'person',
        label TEXT, label_lang TEXT, std_name TEXT,
        forename TEXT, surname TEXT, sex TEXT,
        first_year INTEGER, last_year INTEGER,
        years TEXT, org_ids TEXT, spouse_ids TEXT,
        mother_ids TEXT, father_ids TEXT, loc_ids TEXT,
        orig_names TEXT, std_names TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS orgs (
        id TEXT PRIMARY KEY, uri TEXT, etype TEXT DEFAULT 'org',
        label TEXT, std_name TEXT, surname TEXT,
        alias_of TEXT, org_type TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS name_index (
        name_text TEXT, ssrq_id TEXT, is_orig INTEGER,
        PRIMARY KEY (name_text, ssrq_id))''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_name ON name_index(name_text)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_surname ON persons(surname)')

def block_texts(raw):
    blocks, depth, start = [], 0, -1
    for i, ch in enumerate(raw):
        if ch == '[':
            if depth == 0: start = i + 1
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0 and start != -1:
                blocks.append(raw[start:i]); start = -1
    return blocks

def extract_person(buf, uri, ssrq_id):
    raw = '\n'.join(buf)
    label = label_lang = sex = ''
    for m in TRIPLE_RE.finditer(raw):
        if m.group(2) == 'label': label = m.group(3); label_lang = m.group(4) or ''; break
    for m in TRIPLE_RE.finditer(raw):
        if m.group(2) == 'sex': sex = m.group(3); break
    years = sorted(set(int(y) for y in YEAR_RE.findall(raw) if 1200 <= int(y) <= 2099))
    fv, sv, on, sn = [], [], [], []
    for nb in block_texts(raw):
        fn = FORENAME.search(nb); sm = SURNAME.search(nb); tp = NAMETYPE.search(nb)
        f = fn.group(1) if fn else ''; s = sm.group(1) if sm else ''; t = tp.group(1) if tp else 'std'
        if f: fv.append(f)
        if s: sv.append(s)
        full = ' '.join(filter(None, [f, s])).strip()
        if full:
            if t == 'orig': on.append(full)
            else: sn.append(full)
    fn0, sn0 = fv[0] if fv else '', sv[0] if sv else ''
    std_name = label.split('@')[0] if label else ''
    if not std_name and fn0 and sn0: std_name = fn0 + ' ' + sn0
    elif not std_name and sn0: std_name = sn0
    oi = [m.group(4) for m in URI_ID.finditer(raw) if m.group(1)=='pers' and m.group(2)=='org_id'    and m.group(3)=='org']
    si = [m.group(4) for m in URI_ID.finditer(raw) if m.group(1)=='pers' and m.group(2)=='spouseOf'  and m.group(3)=='per']
    mi = [m.group(4) for m in URI_ID.finditer(raw) if m.group(1)=='pers' and m.group(2)=='motherOf'  and m.group(3)=='per']
    fi = [m.group(4) for m in URI_ID.finditer(raw) if m.group(1)=='pers' and m.group(2)=='fatherOf'  and m.group(3)=='per']
    li = [m.group(4) for m in URI_ID.finditer(raw) if m.group(1)=='pers' and m.group(2)=='residence' and m.group(3)=='loc']
    return dict(id=ssrq_id, uri=uri, etype='person', label=label, label_lang=label_lang,
        std_name=std_name, forename=fn0, surname=sn0, sex=sex,
        first_year=years[0] if years else None, last_year=years[-1] if years else None, years=json.dumps(years),
        org_ids=json.dumps(oi), spouse_ids=json.dumps(si), mother_ids=json.dumps(mi),
        father_ids=json.dumps(fi), loc_ids=json.dumps(li),
        orig_names=json.dumps(on), std_names=json.dumps(sn))

def extract_org(buf, uri, ssrq_id):
    raw = '\n'.join(buf)
    label = ''
    for m in TRIPLE_RE.finditer(raw):
        if m.group(2) == 'label': label = m.group(3); break
    ot = ORG_TYPE.search(raw)
    al = re.search(r'pers:alias_of\s+<[^#]*#per(\w+)>', raw)
    sm = SURNAME.search('\n'.join(block_texts(raw)))
    std_name = label.split('@')[0] if label else ''
    return dict(id=ssrq_id, uri=uri, etype='org', label=label, std_name=std_name,
                surname=sm.group(1) if sm else '', alias_of=al.group(1) if al else '',
                org_type=ot.group(1) if ot else '')

def main():
    conn = sqlite3.connect(DB)
    init_db(conn)
    for t in ('persons','orgs','name_index'): conn.execute('DELETE FROM ' + t)
    conn.commit()
    file_size = os.path.getsize(TTL)
    print('File: ' + str(file_size//1024**2) + ' MiB')
    buf, cu, ce, ci = [], None, None, None
    persons, orgs = [], []
    t0 = os.times().elapsed

    def flush():
        global buf, cu, ce
        if not cu or not buf: return
        if ce == 'per': persons.append(extract_person(buf, cu, ci))
        elif ce == 'org': orgs.append(extract_org(buf, cu, ci))
        buf = []

    with open(TTL, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            m = OUTER_RE.match(line)
            if m:
                flush()
                cu, ce, ci = m.group(1), m.group(2), m.group(3); buf = [line]
            elif cu: buf.append(line)

    flush()
    cp = ('id','uri','etype','label','label_lang','std_name','forename','surname','sex','first_year','last_year','years','org_ids','spouse_ids','mother_ids','father_ids','loc_ids','orig_names','std_names')
    co = ('id','uri','etype','label','std_name','surname','alias_of','org_type')
    for lb, rows, cols in [('persons',persons,cp),('orgs',orgs,co)]:
        if rows:
            ph = ','.join(['?' for _ in cols])
            conn.executemany('INSERT OR REPLACE INTO ' + lb + ' VALUES (' + ph + ')', rows)
            conn.commit()
            print('  ' + lb + ': ' + str(len(rows)))

    print('  Building name index...')
    ni = []; cur = conn.cursor()
    cur.execute('SELECT id,std_name,forename,surname,orig_names,std_names,label FROM persons')
    for ssrq_id, std_name, forename, surname, orig_names, std_names, label in cur:
        def add(n, io=0):
            if n and len(n) > 1: ni.append((n.lower(),ssrq_id,io)); ni.append((n,ssrq_id,io))
        add(std_name)
        if forename and surname: add(forename + ' ' + surname)
        if label: add(label.split('@')[0])
        for n in json.loads(orig_names or '[]'): add(n,1)
        for n in json.loads(std_names or '[]'): add(n)
        if len(ni) >= 2000: cur.executemany('INSERT OR IGNORE INTO name_index VALUES(?,?,?)', ni); ni = []
    if ni: cur.executemany('INSERT OR IGNORE INTO name_index VALUES(?,?,?)', ni)
    conn.commit(); conn.close()
    print('Done in ' + str(round(os.times().elapsed - t0, 1)) + 's - ' + str(len(persons)) + ' persons, ' + str(len(orgs)) + ' orgs')

if __name__ == '__main__': main()