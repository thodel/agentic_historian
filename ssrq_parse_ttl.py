"""
Streaming TTL parser for SSRQ Fuseki dump.
Uses line-by-line detection of blank-node boundaries, then recursive text parsing.
"""
import re, sqlite3, sys, json, os

FRAG_RE  = re.compile(r'.*[#/](per|org|loc)(\w+)$')
OUTER_RE = re.compile(r'^\s*<(http://ssrq-sds-fds\.ch/Register/#(per|org|loc)\d+)>')

def frag_id(uri):
    m = FRAG_RE.match(uri)
    return (m.group(1), m.group(2)) if m else (None, None)


# ─── Recursive parser for blank-node content ─────────────────────────────────

def parse_block(text):
    """Parse [ ... ] block text with quote-aware, namespace-aware colon detection."""
    text = text.strip().strip('[]').strip(';').strip()
    result = {}
    i = 0
    n = len(text)

    while i < n:
        # Skip whitespace before predicate
        while i < n and text[i] in ' \t\n\r':
            i += 1
        if i >= n:
            break

        colon = text.find(':', i)
        if colon == -1:
            break
        pred = text[i:colon].strip()
        if not re.match(r'^[\w.]+$', pred):
            i = colon + 1
            continue

        i = colon + 1
        while i < n and text[i] in ' \t\n\r':
            i += 1
        if i >= n:
            break

        # Extend namespace prefix to full predicate (e.g., 'pers' -> 'pers:forename')
        if text[i].isidentifier():
            j = i
            while j < n and (text[j].isalnum() or text[j] in '_-'):
                j += 1
            local = text[i:j]
            if j < n and text[j] == ':':
                # Another colon follows — keep extending
                pred = f"{pred}:{local}"
                i = j + 1
                while i < n and text[i] in ' \t\n\r':
                    i += 1
                continue  # re-check from current position
            else:
                pred = f"{pred}:{local}"
                i = j
                while i < n and text[i] in ' \t\n\r':
                    i += 1  # skip whitespace before value

        if i >= n:
            break

        if text[i] == '[':
            # Nested blank node
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if text[j] == '[':
                    depth += 1
                elif text[j] == ']':
                    depth -= 1
                j += 1
            result[pred] = parse_block(text[i+1:j-1])
            i = j
        elif text[i] == '<':
            end = text.find('>', i)
            if end == -1:
                i += 1
                continue
            uri = text[i+1:end]
            _, pid = frag_id(uri)
            result[pred] = pid or uri
            i = end + 1
        elif text[i] == '"':
            j = i + 1
            while j < n:
                if text[j] == '\\' and j+1 < n:
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    j += 1
            lit = text[i+1:j]
            i = j + 1
            lang = None
            while i < n and text[i] in ' \t\n\r':
                i += 1
            if i < n and text[i] == '@':
                i += 1
                le = i
                while le < n and text[le].isalpha():
                    le += 1
                lang = text[i:le]
                i = le
            result[pred] = (lit, lang) if lang else lit
        else:
            j = i
            while j < n and text[j] not in ' \t\n\r;]':
                j += 1
            result[pred] = text[i:j].strip()
            i = j

        # Skip semicolon
        while i < n and text[i] in ' \t\n\r;':
            if text[i] == ';':
                i += 1
                break
            i += 1

    return result


# ─── Entity block parser ───────────────────────────────────────────────────────

def parse_entity(lines):
    """Parse raw lines for one entity. Returns EntityBuilder."""
    if not lines:
        return None
    m = OUTER_RE.match(lines[0].rstrip())
    if not m:
        return None

    uri   = m.group(1)
    etype = m.group(2)
    builder = EntityBuilder(uri, etype)

    i = 1
    while i < len(lines):
        raw = lines[i].rstrip()
        i += 1
        if not raw:
            continue

        # Detect blank node start: find [ that follows a predicate pattern
        bracket_pos = -1
        for pi in range(len(raw) - 1, -1, -1):
            if raw[pi] == '[':
                before = raw[:pi].rstrip()
                if before:
                    parts = before.split()
                    if parts and ':' in parts[-1]:
                        bracket_pos = pi
                        break

        if bracket_pos == -1:
            # No blank node — top-level predicate
            if raw.startswith('a '):
                continue
            mu = re.match(r'\s+(\w+):(\w+)\s+<(.+?)>', raw)
            if mu:
                _, pred, uri_val = mu.group(1), mu.group(2), mu.group(3)
                _, pid = frag_id(uri_val)
                if pid:
                    _add_uri_ref(builder, pred, pid)
                continue
            ml = re.match(r'\s+(\w+):(\w+)\s+"([^"]*)"(?:@(\w+))?', raw)
            if ml:
                _, pred, val, lang = ml.group(1), ml.group(2), ml.group(3), ml.group(4)
                if pred == 'label':
                    builder.label = val
                    if lang:
                        builder.label_lang = lang
                elif pred == 'sex':
                    builder.sex = val
                continue
            mk = re.match(r'\s+(\w+):(\w+)\s+([^\s\[\]<"]+)\s*\.?\s*$', raw)
            if mk:
                _, pred, val = mk.group(1), mk.group(2), mk.group(3)
                if pred == 'org_type':
                    builder._org_type = val
                continue
            continue

        # Blank node: collect all lines
        outer_pred_raw = raw[:bracket_pos].rstrip().split()[-1]
        if ':' not in outer_pred_raw:
            continue
        pred_name = outer_pred_raw.split(':')[1]

        j = i
        depth = 1
        block_texts = [raw[bracket_pos+1:]]
        while j < len(lines) and depth > 0:
            l = lines[j].rstrip()
            block_texts.append(l)
            opens  = l.count('[')
            closes = l.count(']')
            depth += opens
            depth -= closes
            j += 1

        block_raw = ' '.join(t.strip() for t in block_texts)
        block_raw = re.sub(r'\s+', ' ', block_raw).strip()
        block_raw = block_raw.strip('[]').strip(';').strip()
        fields = parse_block(block_raw)
        _apply_variant(builder, pred_name, fields)
        i = j

    return builder


def _add_uri_ref(builder, pred, pid):
    if pred == 'org_id':       builder.org_ids.append(pid)
    elif pred == 'spouseOf':   builder.spouse_ids.append(pid)
    elif pred == 'motherOf':   builder.mother_ids.append(pid)
    elif pred == 'fatherOf':   builder.father_ids.append(pid)


def _extract_year(fields):
    for k in ('first_mention', 'death', 'when'):
        v = fields.get(k)
        if v:
            s = v if isinstance(v, str) else (v[0] if isinstance(v, tuple) else '')
            if s.isdigit() and len(s) == 4:
                return int(s)
    for outer in ('format_dates', 'format_date'):
        v = fields.get(outer)
        if isinstance(v, dict):
            r = _extract_year(v)
            if r:
                return r
    return None


def _extract_ref_id(fields):
    for k in ('id', 'org_id', 'location'):
        v = fields.get(k)
        if v:
            return v if isinstance(v, str) else (v[0] if isinstance(v, tuple) else '')
    return None


def _get_str(fields, key):
    v = fields.get(key)
    if not v:
        return ''
    if isinstance(v, str):
        return v
    if isinstance(v, tuple):
        return v[0] or ''
    if isinstance(v, dict):
        for subv in v.values():
            if isinstance(subv, str) and subv:
                return subv
    return ''


def _apply_variant(builder, pred, fields):
    """Apply parsed blank-node fields to the builder."""
    if pred == 'name':
        fn = _get_str(fields, 'forename')
        sn = _get_str(fields, 'surname')
        tp = _get_str(fields, 'type') or 'std'
        full = ' '.join(filter(None, [fn, sn])).strip()
        if full:
            if tp == 'orig':
                builder.orig_names.append(full)
            else:
                builder.std_names.append(full)
        if fn:
            builder.forenames.append(fn)
        if sn:
            builder.surnames.append(sn)

    elif pred == 'first_mentions':
        yr = _extract_year(fields)
        if yr:
            builder.years.append(yr)

    elif pred == 'deaths':
        yr = _extract_year(fields)
        if yr:
            builder.deaths.append(yr)

    elif pred in ('spouseOf', 'motherOf', 'fatherOf'):
        sid = _extract_ref_id(fields)
        if sid:
            if pred == 'spouseOf':   builder.spouse_ids.append(sid)
            elif pred == 'motherOf': builder.mother_ids.append(sid)
            elif pred == 'fatherOf': builder.father_ids.append(sid)

    elif pred == 'org_id':
        oid = _extract_ref_id(fields)
        if oid:
            builder.org_ids.append(oid)

    elif pred == 'residence':
        lid = _extract_ref_id(fields)
        if lid:
            builder.loc_ids.append(lid)


# ─── EntityBuilder ─────────────────────────────────────────────────────────────

class EntityBuilder:
    def __init__(self, uri, etype):
        self.uri = uri
        self.etype = etype
        self.label = None
        self.label_lang = None
        self.forenames  = []
        self.surnames   = []
        self.std_names  = []
        self.orig_names = []
        self.years      = []
        self.deaths     = []
        self.sex        = None
        self.org_ids    = []
        self.loc_ids    = []
        self.spouse_ids = []
        self.mother_ids = []
        self.father_ids = []
        self._org_type  = ''

    def to_record(self):
        all_yrs = sorted(set(self.years + self.deaths))
        fn = self.forenames[0] if self.forenames else ''
        sn = self.surnames[0]  if self.surnames  else ''
        std_name = ' '.join(filter(None, [fn, sn])).strip()
        if not std_name and self.label:
            std_name = self.label.split('@')[0]
        ssrq_id = self.uri.split('/')[-1]
        rec = {
            'id':         ssrq_id,
            'uri':        self.uri,
            'etype':      self.etype,
            'label':      self.label or '',
            'label_lang': self.label_lang or '',
            'std_name':   std_name,
            'forename':   fn,
            'surname':    sn,
            'sex':        self.sex or '',
            'first_year': all_yrs[0] if all_yrs else None,
            'last_year':  all_yrs[-1] if all_yrs else None,
            'years':      json.dumps(all_yrs),
            'org_ids':    json.dumps(self.org_ids),
            'spouse_ids': json.dumps(self.spouse_ids),
            'mother_ids': json.dumps(self.mother_ids),
            'father_ids': json.dumps(self.father_ids),
            'loc_ids':    json.dumps(self.loc_ids),
            'orig_names': json.dumps(self.orig_names),
            'std_names':  json.dumps(self.std_names),
        }
        if self.etype == 'org':
            rec['surname']  = sn
            rec['std_name'] = std_name or (self.label.split('@')[0] if self.label else '')
            rec['alias_of'] = ''
            rec['org_type'] = self._org_type or ''
        return rec


# ─── DB helpers ────────────────────────────────────────────────────────────────

def init_db(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS persons (
        id TEXT PRIMARY KEY, uri TEXT, etype TEXT DEFAULT 'person',
        label TEXT, label_lang TEXT, std_name TEXT, forename TEXT, surname TEXT, sex TEXT,
        first_year INTEGER, last_year INTEGER,
        years TEXT, org_ids TEXT, spouse_ids TEXT, mother_ids TEXT, father_ids TEXT,
        loc_ids TEXT, orig_names TEXT, std_names TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS orgs (
        id TEXT PRIMARY KEY, uri TEXT, etype TEXT DEFAULT 'org',
        label TEXT, std_name TEXT, surname TEXT, alias_of TEXT, org_type TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS locs (
        id TEXT PRIMARY KEY, uri TEXT, etype TEXT DEFAULT 'loc', label TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS name_index (
        name_text TEXT, ssrq_id TEXT, is_orig INTEGER, PRIMARY KEY(name_text, ssrq_id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_name ON name_index(name_text)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_surname ON persons(surname)")

def flush_persons(batch, conn):
    cur = conn.cursor()
    for r in batch:
        cur.execute("""INSERT OR REPLACE INTO persons VALUES(
            :id,:uri,:etype,:label,:label_lang,:std_name,:forename,:surname,:sex,
            :first_year,:last_year,:years,:org_ids,:spouse_ids,:mother_ids,:father_ids,
            :loc_ids,:orig_names,:std_names)""", r)
    conn.commit()

def flush_orgs(batch, conn):
    cur = conn.cursor()
    for r in batch:
        r.setdefault('alias_of','')
        r.setdefault('org_type','')
        cur.execute("""INSERT OR REPLACE INTO orgs VALUES(
            :id,:uri,:etype,:label,:std_name,:surname,:alias_of,:org_type)""", r)
    conn.commit()

def build_name_index(conn):
    print("  Building name index...", file=sys.stderr)
    cur = conn.cursor()
    batch = []
    cur.execute("SELECT id, std_name, forename, surname, orig_names, std_names, label FROM persons")
    for row in cur:
        ssrq_id, std_name, forename, surname, orig_names, std_names, label = row
        def add(n, is_o=0):
            if n and len(n) > 1:
                batch.append((n.lower(), ssrq_id, is_o))
                batch.append((n, ssrq_id, is_o))
        add(std_name)
        if forename and surname:
            add(f"{forename} {surname}")
        if label:
            add(label.split('@')[0])
        for n in json.loads(orig_names or '[]'):
            add(n, 1)
        for n in json.loads(std_names or '[]'):
            add(n)
        if len(batch) >= 2000:
            cur.executemany("INSERT OR IGNORE INTO name_index VALUES(?,?,?)", batch)
            batch = []
    if batch:
        cur.executemany("INSERT OR IGNORE INTO name_index VALUES(?,?,?)", batch)
    conn.commit()


def build_index(ttl_path, db_path):
    conn = sqlite3.connect(db_path)
    init_db(conn)
    for t in ('persons','orgs','locs','name_index'):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()

    file_size = os.path.getsize(ttl_path)
    per_b, org_b = [], []
    pc = oc = 0
    buf = []
    cur_uri = cur_etype = None
    total = 0
    print(f"  Parsing {file_size//1024**2} MB...", file=sys.stderr)

    with open(ttl_path, 'r', encoding='utf-8', errors='replace') as f:
        for raw in f:
            total += len(raw)
            line = raw.rstrip()
            m = OUTER_RE.match(line)
            if m:
                if cur_uri and buf:
                    b = parse_entity(buf)
                    if b:
                        rec = b.to_record()
                        if b.etype == 'per':
                            per_b.append(rec)
                            if len(per_b) >= 500:
                                flush_persons(per_b, conn)
                                pc += len(per_b)
                                per_b = []
                        elif b.etype == 'org':
                            org_b.append(rec)
                            if len(org_b) >= 500:
                                flush_orgs(org_b, conn)
                                oc += len(org_b)
                                org_b = []
                cur_uri = m.group(1)
                cur_etype = m.group(2)
                buf = [line]
            else:
                buf.append(line)
            if total % 10_000_000 == 0:
                print(f"  {total/file_size*100:.0f}%  persons={pc}  orgs={oc}", file=sys.stderr, flush=True)

        if cur_uri and buf:
            b = parse_entity(buf)
            if b:
                rec = b.to_record()
                if b.etype == 'per':   per_b.append(rec)
                elif b.etype == 'org': org_b.append(rec)

    for label, batch, flush_fn, counter in [
        ('persons', per_b, flush_persons, pc),
        ('orgs',    org_b, flush_orgs,    oc),
    ]:
        if batch:
            flush_fn(batch, conn)
            counter += len(batch)
            print(f"  {label}: {counter}", file=sys.stderr)

    build_name_index(conn)
    return pc, oc, 0


if __name__ == '__main__':
    import time
    t0 = time.time()
    ttl = sys.argv[1] if len(sys.argv) > 1 else '/data/ssrq.ttl'
    db  = sys.argv[2] if len(sys.argv) > 2 else '/data/ssrq.db'
    p, o, l = build_index(ttl, db)
    print(f"Done in {time.time()-t0:.1f}s — {p} persons, {o} orgs, {l} locs")