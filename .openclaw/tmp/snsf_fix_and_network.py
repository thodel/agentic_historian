import csv, urllib.request, re, json
from collections import Counter, defaultdict

URL = "https://data.snf.ch/datasets/grants_with_abstracts.csv"
response = urllib.request.urlopen(URL, timeout=60)
lines = response.read().decode('utf-8', errors='replace').splitlines()
reader = csv.DictReader(lines, delimiter=';')

DH_DISCIPLINES = {
    'Information Technology', 'Applied linguistics', 'Communication sciences',
    'German and English languages and literature', 'Romance languages and literature',
    'Other languages and literature', 'General history (without pre-and early history)',
    'Swiss history', 'Ancient history and Classical studies', 'Archaeology',
    'Prehistory', 'Visual arts and Art history', 'Philosophy',
    'Ecclesiastical history', 'Linguistics and literature, philosophy',
    'Theology & religious studies, history, classical studies, archaeology, prehistory and early history',
    'Arts', 'Musicology', 'Theatre and Cinema',
}

DH_PATTERNS = [
    'digital human', ' dh ', 'digitis', 'digitaliz',
    'text mining', 'corpus linguist', ' nlp ', 'natural language process',
    'language model', ' llm ', 'large language model', 'artificial intelligence',
    'machine learning', 'deep learning', 'neural network',
    'computational philology', 'computational ling', 'language technology',
    'speech process', ' ocr ', 'handwriting recognition', 'historical corpora',
    'knowledge graph', 'semantic web', 'linked data', 'digital archiv',
    'digital schol', 'digital memory', 'digital herit', 'digital cultural',
    'media studies', 'digital media', 'digital edition', 'digitised edition',
    'digitized edition', 'electronic text', 'digital corpus',
    'cultural analyt', 'heritage comput', 'computational history',
    'lexicography', 'lexicographic', 'philology', 'corpus building',
    'social media analys', 'automatic analys', 'computational semant',
    'data-driven humanit', 'data-driven ling', 'automatic text',
    'digitization project', 'digitisation project', 'digital approach',
    'quantitative text', 'quantitative ling', ' ai ',
    'artificial intelligence meth', 'ai-assisted', 'ai-based',
    'transcription automat', 'named entity', 'information extract',
    'linguistic corpora', 'parallel corpus', 'text corpora',
    'virtual reconstruct', '3d model', 'digital reconstruct',
    'linguistic annot', 'annotation automat', 'language resource',
    'etext', 'corpus-based', 'corpus building', 'digital library',
    'automatic translat', 'machine translat', 'neural translat',
    'digital image analys', 'computer vision', 'image process',
    'data visuali', 'network analys', 'complex network analys',
    'digital epigraph', 'epigraphic', 'paleographic', 'codicolog',
    'text-encoding', 'tei xml', 'hierarchical encod',
]

compiled_patterns = [re.compile(p, re.IGNORECASE) for p in DH_PATTERNS]

def is_dh_row(row):
    disc = row.get('MainDiscipline','').strip()
    text = (row.get('Keywords','') + ' ' + row.get('Abstract','') + ' ' +
            row.get('Title','') + ' ' + row.get('TitleEnglish','')).lower()
    if disc in DH_DISCIPLINES:
        return True
    for p in compiled_patterns:
        if p.search(text):
            return True
    return False

all_rows = list(reader)
dh_all = [r for r in all_rows if is_dh_row(r)]
dh_active = [r for r in dh_all if r.get('State','').strip() in ('Ongoing','Approved')]

# ─── 1. PI affiliation corrections ────────────────────────────
print("=== PI AFFILIATION CORRECTIONS ===")
# Look up all active DH grants for key PIs
pi_lookups = {
    'Miroslav Novak': 'Miroslav Novak',
    'Matthias Schmidt': 'Matthias Schmidt',
    'Lorenza Mondada': 'Lorenza Mondada',
    'Pascal Fua': 'Pascal Fua',
}
for target_name, search_name in pi_lookups.items():
    hits = []
    for r in dh_active:
        pi = r.get('ResponsibleApplicantName','').strip()
        if pi.lower().replace(' ','') == search_name.lower().replace(' ',''):
            hits.append(r)
    insts = list(set(r.get('ResearchInstitution','') for r in hits))
    print(f"  {target_name}: {insts}")
    for r in hits[:3]:
        print(f"    - {r.get('TitleEnglish','')[:60]} | {r.get('ResearchInstitution','')}")

print()

# ─── 2. Up-and-coming topics (growth analysis) ─────────────────
print("=== UP-AND-COMING DH TOPICS ===")
recent = [r for r in dh_active
          if r.get('EffectiveGrantStartDate','') and
          int(r.get('EffectiveGrantStartDate','0')[:4] or 0) >= 2022]
past   = [r for r in dh_active
          if r.get('EffectiveGrantStartDate','') and
          int(r.get('EffectiveGrantStartDate','0')[:4] or 0) < 2022]

def kw_counter(rows):
    c = Counter()
    for r in rows:
        for kw in re.split(r'[,;]', r.get('Keywords','')):
            kw = kw.strip().lower()
            if kw and len(kw) > 2:
                c[kw] += 1
    return c

recent_kw = kw_counter(recent)
past_kw   = kw_counter(past)

# Topic emergence score: recent only + growth
emerging = {}
for kw, cnt in recent_kw.items():
    past_cnt = past_kw.get(kw, 0)
    if cnt >= 5:
        score = round((cnt + 1) / (past_cnt + 1), 2)
        emerging[kw] = (score, cnt, past_cnt)

sorted_emerging = sorted(emerging.items(), key=lambda x: x[1][0]*x[1][1], reverse=True)
print("Top emerging topics (recent 2022+, by growth ratio × volume):")
for kw, (ratio, cnt, past) in sorted_emerging[:40]:
    bar = '█' * min(int(ratio), 20)
    print(f"  {ratio:>5.1f}x [{cnt:>3} rec / {past:>3} past] {kw} {bar}")

print()

# ─── 3. Sinergia grants ────────────────────────────────────────
print("=== SINERGIA DH GRANTS ===")
sinergia = sorted(
    [r for r in dh_active if r.get('FundingInstrumentPublished','') == 'Sinergia'],
    key=lambda x: -float(x.get('AmountGrantedAllSets','0').replace(',','') or 0)
)
for r in sinergia:
    amt = r.get('AmountGrantedAllSets','')
    title = (r.get('TitleEnglish','') or r.get('Title',''))[:90]
    pi = r.get('ResponsibleApplicantName','')
    inst = r.get('ResearchInstitution','')
    start = r.get('EffectiveGrantStartDate','')[:4]
    end = r.get('EffectiveGrantEndDate','')[:4]
    kws = r.get('Keywords','')[:100]
    disc = r.get('MainDiscipline','')
    print(f"  [{amt} CHF | {start}–{end} | {disc}]")
    print(f"    {title}")
    print(f"    PI: {pi} | {inst}")
    print(f"    Keywords: {kws}")
    print()

# ─── 4. Major grants (>= CHF 300k, project funding instruments) ─
print("=== MAJOR DH GRANTS (>= CHF 300k) ===")
major_instruments = {
    'Project funding','Sinergia','Weave/Lead Agency',
    'Ambizione','Eccellenza','Eccellenza grant',
    'SNSF Advanced Grants','SNSF Starting Grants','PRIMA','SPIRIT'
}
major = sorted(
    [r for r in dh_active
     if r.get('FundingInstrumentPublished','') in major_instruments
     and float(r.get('AmountGrantedAllSets','0').replace(',','') or 0) >= 300000],
    key=lambda x: -float(x.get('AmountGrantedAllSets','0').replace(',','') or 0)
)
for r in major[:25]:
    amt = r.get('AmountGrantedAllSets','')
    title = (r.get('TitleEnglish','') or r.get('Title',''))[:80]
    pi = r.get('ResponsibleApplicantName','')
    inst = r.get('ResearchInstitution','')
    fi = r.get('FundingInstrumentPublished','')
    start = r.get('EffectiveGrantStartDate','')[:4]
    kws = r.get('Keywords','')[:80]
    print(f"  [{amt} CHF | {fi} | {start}] {title}")
    print(f"    PI: {pi} | {inst}")
    print(f"    Keywords: {kws}")
    print()