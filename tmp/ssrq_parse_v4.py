#!/usr/bin/env python3
import sqlite3, json, os

TTL = "/home/dh/resources/ssrq__fuseki_042810.ttl"
DB  = "/home/dh/.openclaw/tmp/ssrq_v4.db"

def init_db(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS persons (
        id TEXT PRIMARY KEY, uri TEXT, etype TEXT DEFAULT "person",
        label TEXT, label_lang TEXT, std_name TEXT,
        forename TEXT, surname TEXT, sex TEXT,
        first_year INTEGER, last_year INTEGER,
        years TEXT, org_ids TEXT, spouse_ids TEXT,
        mother_ids TEXT, father_ids TEXT, loc_ids TEXT,
        orig_names TEXT, std_names TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS orgs (
        id TEXT PRIMARY KEY, uri TEXT, etype TEXT DEFAULT "org",
        label TEXT, std_name TEXT, surname TEXT,
        alias_of TEXT, org_type TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS name_index (
        name_text TEXT, ssrq_id TEXT, is_orig INTEGER,
        PRIMARY KEY (name_text, ssrq_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON name_index(name_text)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_surname ON persons(surname)")

def qv(s, start=0):
    q1 = s.find('"', start)
    if q1 < 0: return "", "", -1
    q2 = s.find('"', q1+1)
    if q2 < 0: return "", "", -1
    at = s.find('@', q2)
    lang = ""
    val = s[q1+1:q2]
    if at >= 0 and at < len(s):
        sp = s.find(' ', at)
        if sp < 0: sp = len(s)
        lang = s[at+1:sp].strip()
    return val, lang, q2

def extract_years(text):
    ys = []
    i = 0
    while i < len(text):
        if text[i].isdigit():
            end = i
            while end < len(text) and text[end].isdigit() and end < i+4:
                end += 1
            if end - i == 4:
                y = int(text[i:end])
                if 1200 <= y <= 2099: ys.append(y)
            i = end
        else:
            i += 1
    return sorted(set(ys))

def name_blocks(raw):
    blocks, depth = [], 0
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == '[':
            depth += 1
            if depth == 1: blocks.append('')
        elif ch == ']':
            depth -= 1
        elif depth > 1:
            if blocks: blocks[-1] += ch
        i += 1
    return blocks

def get_val(nb, prefix):
    idx = nb.find(prefix)
    if idx < 0: return ''
    v, _, _ = qv(nb, idx)
    return v

def process_person(eid, uri, raw):
    label = label_lang = sex = ''
    for line in raw.split('\n'):
        line = line.strip()
        if ':label' in line:
            v, lang, _ = qv(line)
            if v: label = v; label_lang = lang
        elif ':sex' in line:
            v, _, _ = qv(line)
            if v: sex = v
    years = extract_years(raw)
    fv, sv, on, sn = [], [], [], []
    for nb in name_blocks(raw):
        f = get_val(nb, 'forename')
        s = get_val(nb, 'surname')
        t = get_val(nb, 'type')
        if f: fv.append(f)
        if s: sv.append(s)
        full = (f+' '+s).strip()
        if full:
            if t == 'orig': on.append(full)
            else: sn.append(full)
    fn0 = fv[0] if fv else ''
    sn0 = sv[0] if sv else ''
    std_name = label.split('@')[0] if label else ''
    if not std_name and fn0 and sn0: std_name = fn0+' '+sn0
    elif not std_name and sn0: std_name = sn0
    oi = []
    for line in raw.split('\n'):
        if 'pers:org_id' in line and '#org' in line:
            h = line.find('#org')
            e = line.find('>', h)
            if e >= 0: oi.append(line[h+3:e].strip())
    return (eid, uri, 'person', label, label_lang, std_name,
            fn0, sn0, sex,
            years[0] if years else None, years[-1] if years else None,
            json.dumps(years), json.dumps(oi),
            '[]', '[]', '[]', '[]',
            json.dumps(on), json.dumps(sn))

def main():
    conn = sqlite3.connect(DB)
    init_db(conn)
    for t in ("persons","orgs","name_index"): conn.execute("DELETE FROM "+t)
    conn.commit()
    sz = os.path.getsize(TTL)//1024**2
    print("File: "+str(sz)+" MiB")
    buf, cur_type, cur_id, cur_uri = [], None, None, ''
    persons, orgs = [], []
    t0 = os.times().elapsed; count = 0
    MARK = 'http://ssrq-sds-fds.ch/Register/#'
    ML = len(MARK)

    with open(TTL, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            count += 1
            if count % 500000 == 0:
                print("  "+str(count//1000)+"k lines, "+str(round(os.times().elapsed-t0,1))+"s")
            if not cur_type:
                h = line.find(MARK)
                if h >= 0:
                    e = line.find('>', h)
                    if e >= 0:
                        cur_uri = line[h:e+1]
                        pound = line.find('#', h)
                        if pound >= 0 and pound < e:
                            entity = line[pound+1:e]
                            colon = entity.find(':')
                            if colon >= 0:
                                cur_type = entity[:colon]
                                cur_id = entity[colon+1:]
                            else:
                                cur_type = entity
                                cur_id = ''
                            buf = [line]
            else:
                buf.append(line)
                ls = line.strip()
                if ls == '.':
                    raw = ''.join(buf)
                    if cur_type == 'per':
                        persons.append(process_person(cur_id, cur_uri, raw))
                    cur_type = None; cur_id = None; buf = []

    print("  persons: "+str(len(persons)))
    cp = ('id','uri','etype','label','label_lang','std_name','forename','surname','sex',
          'first_year','last_year','years','org_ids','spouse_ids','mother_ids','father_ids','loc_ids','orig_names','std_names')
    if persons:
        ph = ','.join(['?' for _ in cp])
        conn.executemany('INSERT OR REPLACE INTO persons VALUES ('+ph+')', persons)
        conn.commit()
        print("  inserted "+str(len(persons))+" persons")
    print("  Building name_index...")
    ni = []
    for row in conn.execute('SELECT id,std_name,forename,surname,orig_names,std_names,label FROM persons'):
        sid, std_name, forename, surname, on_js, sn_js, label = row
        def add(n, io=0):
            if n and len(n) > 1: ni.append((n.lower(),sid,io)); ni.append((n,sid,io))
        add(std_name)
        if forename and surname: add(forename+' '+surname)
        if label: add(label.split('@')[0])
        for n in json.loads(on_js or '[]'): add(n,1)
        for n in json.loads(sn_js or '[]'): add(n)
        if len(ni) >= 2000:
            conn.executemany('INSERT OR IGNORE INTO name_index VALUES(?,?,?)', ni); ni = []
    if ni: conn.executemany('INSERT OR IGNORE INTO name_index VALUES(?,?,?)', ni)
    conn.commit(); conn.close()
    print("Done in "+str(round(os.times().elapsed-t0,1))+"s")

if __name__ == '__main__': main()