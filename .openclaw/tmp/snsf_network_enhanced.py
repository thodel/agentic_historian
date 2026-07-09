import csv, urllib.request, re, json
from collections import Counter, defaultdict
import os

URL = "https://data.snf.ch/datasets/grants_with_abstracts.csv"
REPORT_PATH = "/home/dh/repos/internal-reporting/reports/v0.1_dh-field-overview-2026-06/data"

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
    ' ai ',
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

CORRECTIONS = {
    'Miroslav Novak': 'University of Bern – BE',
    'Matthias Schmidt': 'University of Basel – BS',
}

def norm_name(s):
    return s.lower().replace(' ','').replace('.','').replace('-','')

def is_dh(row):
    disc = row.get('MainDiscipline','').strip()
    if disc in DH_DISCIPLINES:
        return True
    text = (row.get('Keywords','') + ' ' + row.get('Abstract','') + ' ' +
            row.get('Title','') + ' ' + row.get('TitleEnglish','')).lower()
    return any(p.search(text) for p in compiled_patterns)

print("Downloading CSV...")
response = urllib.request.urlopen(URL, timeout=60)
lines = response.read().decode('utf-8', errors='replace').splitlines()
rows = list(csv.DictReader(lines, delimiter=';'))
print(f"Total rows: {len(rows)}")

active_dh = [r for r in rows if r.get('State','').strip() in ('Ongoing','Approved') and is_dh(r)]
print(f"Active DH grants: {len(active_dh)}")

# PI institution lookup
pi_inst = {}
for r in active_dh:
    pi = r.get('ResponsibleApplicantName','').strip()
    if not pi:
        continue
    norm_pi = norm_name(pi)
    fixed = next((v for k,v in CORRECTIONS.items() if norm_name(k) == norm_pi), None)
    pi_inst[pi] = fixed or r.get('ResearchInstitution','').strip()

# ── Fine-grained sub-clusters ──────────────────────────────────
SUB_CLUSTERS = {
    'AI/ML Methods':           ['machine learning','deep learning','neural network','neural networks',
                                 'artificial intelligence','large language model','llm','large language models',
                                 'generative ai','optimization','reinforcement learning'],
    'NLP & Text Mining':       ['nlp','natural language processing','text mining','corpus linguistics',
                                 'language model','lexicography','philology','historical linguistics',
                                 'computational linguistics','named entity','information extraction'],
    'Computer Vision & Imaging':['computer vision','image processing','image analysis','3d model',
                                  'virtual reconstruction','digital reconstruction','photogrammetry'],
    'Digital Archives & Editions':['digital archive','digital library','tei xml','text-encoding',
                                   'digital edition','digitised edition','digitized edition','electronic text'],
    'Cultural Heritage & Archaeology':['digital heritage','heritage computing','archaeology',
                                        'paleographic','epigraphic','codicology','digital epigraph',
                                        'remote sensing'],
    'Climate & Environmental DH':['climate change','environmental history','sustainability','remote sensing',
                                   'environmental'],
    'Music & Sound AI':         ['music','audio','musicology','sound','audio analysis','musical'],
    'Media & Communication':    ['media studies','digital media','social media','communication sciences'],
    'Ethics & AI Governance':   ['ethics','governance','responsible ai','ai ethics','responsible ai'],
    'Data Visualisation & Networks':['network analysis','data visualisation','visualization','complex networks'],
}

def assign_clusters(kws):
    kw = kws.lower()
    return [c for c, terms in SUB_CLUSTERS.items() if any(t in kw for t in terms)] or ['Other DH']

cluster_ct = Counter()
for r in active_dh:
    cluster_ct.update(assign_clusters(r.get('Keywords','')))

fg_csv = f'{REPORT_PATH}/fine_granular_clusters.csv'
with open(fg_csv, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['subcluster','grant_count'])
    for sc, ct in cluster_ct.most_common():
        w.writerow([sc, ct])
print(f"Written: {fg_csv}")

# ── Up-and-coming topics with PIs ──────────────────────────────
recent = [r for r in active_dh if r.get('EffectiveGrantStartDate','') and
          int(r.get('EffectiveGrantStartDate','0')[:4] or 0) >= 2022]
past   = [r for r in active_dh if r.get('EffectiveGrantStartDate','') and
          int(r.get('EffectiveGrantStartDate','0')[:4] or 0) < 2022]

def kw_ct(rows):
    c = Counter()
    for r in rows:
        for kw in r.get('Keywords','').split(','):
            kw = kw.strip().lower()
            if kw and len(kw) > 2:
                c[kw] += 1
    return c

rk = kw_ct(recent)
pk = kw_ct(past)

upcoming = {}
for kw, cnt in rk.items():
    op = pk.get(kw, 0)
    if cnt >= 5:
        upcoming[kw] = (round((cnt+1)/(op+1),2), cnt, op)

sorted_up = sorted(upcoming.items(), key=lambda x: x[1][0]*x[1][1], reverse=True)[:30]

topic_pi_rows = []
for kw, (ratio, cnt, op) in sorted_up:
    pi_ct = Counter()
    for r in recent:
        if kw in [k.strip().lower() for k in r.get('Keywords','').split(',')]:
            pi = r.get('ResponsibleApplicantName','').strip()
            if pi:
                pi_ct[pi] += 1
    for pi, pc in pi_ct.most_common(3):
        topic_pi_rows.append({
            'keyword': kw, 'growth_ratio': ratio, 'recent_count': cnt,
            'older_count': op, 'pi': pi, 'pi_grants': pc,
            'institution': pi_inst.get(pi,'')
        })

up_csv = f'{REPORT_PATH}/upcoming_topics_pi.csv'
with open(up_csv, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=['keyword','growth_ratio','recent_count','older_count','pi','pi_grants','institution'])
    w.writeheader()
    w.writerows(topic_pi_rows)
print(f"Written: {up_csv}")

# ── PI‑PI co-authorship edges ───────────────────────────────────
# CoResponsibleApplicantName may contain ';'-separated names
pi_pair_ct = Counter()
for r in active_dh:
    pis = [r.get('ResponsibleApplicantName','').strip()] + \
          [c.strip() for c in (r.get('CoResponsibleApplicantName','') or '').split(';') if c.strip()]
    pis = [p for p in pis if p]
    for i in range(len(pis)):
        for j in range(i+1, len(pis)):
            a, b = sorted([pis[i], pis[j]])
            pi_pair_ct[(a, b)] += 1

pipi_csv = f'{REPORT_PATH}/network_pi_pi_edges.csv'
with open(pipi_csv, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['source_pi','target_pi','weight'])
    for (a, b), wgt in sorted(pi_pair_ct.items(), key=lambda x: -x[1]):
        w.writerow([a, b, wgt])
print(f"Written: {pipi_csv} ({len(pi_pair_ct)} edges)")

# ── Sinergia grants dropdown ────────────────────────────────────
sinergia = sorted(
    [r for r in active_dh if r.get('FundingInstrumentPublished','') == 'Sinergia'],
    key=lambda x: -float(x.get('AmountGrantedAllSets','0').replace(',','') or 0)
)
sin_csv = f'{REPORT_PATH}/sinergia_grants_dropdown.csv'
with open(sin_csv, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=[
        'label','grant_id','title','pi','institution','amount_chf','start_year','end_year','keywords','discipline','url'
    ], extrasaction='ignore')
    w.writeheader()
    for r in sinergia:
        gid = r.get('GrantNumberString','').strip()
        title = (r.get('TitleEnglish','') or r.get('Title','')).strip()[:120]
        label = f"[Sinergia | CHF {float(r.get('AmountGrantedAllSets','0').replace(',','') or 0):,.0f}] {title[:80]}"
        w.writerow({**r, 'label': label, 'title': title, 'url': f'https://data.snf.ch/grants/{gid}'})
print(f"Written: {sin_csv} ({len(sinergia)} entries)")

# Print key corrections for verification
print()
print("PI corrections:")
for pi, inst in pi_inst.items():
    if pi in CORRECTIONS:
        print(f"  {pi} → {inst}")
print(f"\nTotal active DH grants: {len(active_dh)}")
print(f"Total Sinergia DH grants: {len(sinergia)}")
print(f"Top emerging topics: {[kw for kw,_ in sorted_up[:10]]}")