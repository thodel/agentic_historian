import csv, urllib.request, re
from collections import Counter
import os

URL = "https://data.snf.ch/datasets/grants_with_abstracts.csv"
REPORT_PATH = "/home/dh/repos/internal-reporting/reports/v0.1_dh-field-overview-2026-06/data"

DH_DISCS = {
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
CP = [re.compile(p, re.IGNORECASE) for p in DH_PATTERNS]

# PI corrections — key must match the exact name in the data
CORRECTIONS = {
    'Miroslav Novak':      'University of Bern \u2013 BE',  # will also match "Miroslav Nov\u00e1k"
    'Miroslav Novák':      'University of Bern \u2013 BE',
    'Matthias Schmidt':    'University of Basel \u2013 BS',
}

def norm(s):
    return s.lower().replace(' ','').replace('.','').replace('-','').replace('\u0327','')

def is_dh(r):
    if r.get('MainDiscipline','').strip() in DH_DISCS:
        return True
    t = (r.get('Keywords','')+' '+r.get('Abstract','')+' '+r.get('Title','')+' '+r.get('TitleEnglish','')).lower()
    return any(p.search(t) for p in CP)

print("Downloading...")
rows = list(csv.DictReader(
    urllib.request.urlopen(URL, timeout=60).read().decode('utf-8', errors='replace').splitlines(),
    delimiter=';'
))
print(f"Total rows: {len(rows)}")

active = [r for r in rows if r.get('State','').strip() in ('Ongoing','Approved') and is_dh(r)]
print(f"Active DH grants: {len(active)}")

# ── PI institution lookup ────────────────────────────────────────
pi_inst = {}
for r in active:
    pi = r.get('ResponsibleApplicantName','').strip()
    if not pi:
        continue
    fixed = next((v for k,v in CORRECTIONS.items() if norm(k) == norm(pi)), None)
    pi_inst[pi] = fixed or r.get('ResearchInstitution','').strip()

# Verify corrections applied
for pi, inst in pi_inst.items():
    if 'Novak' in pi or 'Schmidt' in pi:
        print(f"  PI: '{pi}' → {inst}")

# ── Fine-grained sub-clusters ───────────────────────────────────
SUBS = {
    'AI/ML Methods':              ['machine learning','deep learning','neural network','neural networks',
                                    'artificial intelligence','large language model','llm','large language models',
                                    'generative ai','optimization','reinforcement learning','data science'],
    'NLP & Text Mining':          ['nlp','natural language processing','text mining','corpus linguist',
                                    'language model','lexicography','philology','historical linguist',
                                    'computational linguistics','named entity','information extraction'],
    'Computer Vision & Imaging':  ['computer vision','image processing','image analysis','3d model',
                                    'virtual reconstruction','digital reconstruction','photogrammetry'],
    'Digital Archives & Editions':['digital archive','digital library','tei xml','text-encoding',
                                    'digital edition','digitised edition','digitized edition','electronic text'],
    'Cultural Heritage & Archaeology':['digital heritage','heritage comput','archaeology',
                                        'paleographic','epigraphic','codicology','digital epigraph','remote sensing'],
    'Climate & Environmental DH': ['climate change','environmental history','sustainability','remote sensing'],
    'Music & Sound AI':           ['music','audio','musicology','sound','audio analysis','musical'],
    'Media & Communication':      ['media studies','digital media','social media','communication sciences'],
    'Ethics & AI Governance':     ['ethics','governance','responsible ai','ai ethics'],
    'Data Vis & Network Analysis':['network analys','complex network analys','data visuali','visualization'],
}

def clusters(kws):
    kw = kws.lower()
    return [c for c,ts in SUBS.items() if any(t in kw for t in ts)] or ['Other DH']

sct = Counter()
for r in active:
    sct.update(clusters(r.get('Keywords','')))

fg = f'{REPORT_PATH}/fine_granular_clusters.csv'
with open(fg,'w',newline='',encoding='utf-8') as f:
    w=csv.writer(f); w.writerow(['subcluster','grant_count'])
    for sc,ct in sct.most_common(): w.writerow([sc,ct])
print(f"Written: {fg}")

# ── Up-and-coming topics with PIs ───────────────────────────────
recent = [r for r in active if r.get('EffectiveGrantStartDate','') and
          int(r.get('EffectiveGrantStartDate','0')[:4] or 0) >= 2022]
past   = [r for r in active if r.get('EffectiveGrantStartDate','') and
          int(r.get('EffectiveGrantStartDate','0')[:4] or 0) < 2022]

def kw_ct(rows):
    c=Counter()
    for r in rows:
        for kw in r.get('Keywords','').split(','):
            kw=kw.strip().lower()
            if kw and len(kw)>2: c[kw]+=1
    return c

rk=kw_ct(recent); pk=kw_ct(past)
up={}
for kw,cnt in rk.items():
    op=pk.get(kw,0)
    if cnt>=5: up[kw]=(round((cnt+1)/(op+1),2),cnt,op)
sup=sorted(up.items(),key=lambda x:x[1][0]*x[1][1],reverse=True)[:30]

tprows=[]
for kw,(ratio,cnt,op) in sup:
    pc=Counter()
    for r in recent:
        if kw in [k.strip().lower() for k in r.get('Keywords','').split(',')]:
            pi=r.get('ResponsibleApplicantName','').strip()
            if pi: pc[pi]+=1
    for pi,pcnt in pc.most_common(3):
        tprows.append({'keyword':kw,'growth_ratio':ratio,'recent_count':cnt,
                       'older_count':op,'pi':pi,'pi_grants':pcnt,
                       'institution':pi_inst.get(pi,'')})

upf=f'{REPORT_PATH}/upcoming_topics_pi.csv'
with open(upf,'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=['keyword','growth_ratio','recent_count','older_count',
                                    'pi','pi_grants','institution'])
    w.writeheader(); w.writerows(tprows)
print(f"Written: {upf}")

# ── Co-PI network ───────────────────────────────────────────────
# CoResponsibleApplicantName is empty; use Sinergia as proxy:
# Sinergia = 2–4 institutions working together.
# Build edges between every PI pair on the same Sinergia grant.
# Also: build edges between PIs sharing keywords (topic-based co-occurrence)
pi_grants = {}   # pi → list of (grant_id, keywords, institution)
for r in active:
    pi = r.get('ResponsibleApplicantName','').strip()
    if not pi: continue
    if pi not in pi_grants: pi_grants[pi] = []
    pi_grants[pi].append({
        'grant': r.get('GrantNumberString','').strip(),
        'kws': r.get('Keywords','').lower(),
        'inst': pi_inst.get(pi,''),
        'fi': r.get('FundingInstrumentPublished',''),
    })

# Method A: Sinergia/Weave edges (institutional collaboration proxy)
sin_weave = [r for r in active if r.get('FundingInstrumentPublished','') in ('Sinergia','Weave/Lead Agency')]
sin_pi_edges = Counter()
for r in sin_weave:
    pis = [r.get('ResponsibleApplicantName','').strip()]
    co_str = r.get('CoResponsibleApplicantName','') or ''
    pis += [c.strip() for c in co_str.split(';') if c.strip()]
    pis = [p for p in pis if p]
    for i in range(len(pis)):
        for j in range(i+1, len(pis)):
            a,b = sorted([pis[i], pis[j]])
            sin_pi_edges[(a,b)] += 1

# Method B: topic-based co-PI edges (same keyword used by different PIs)
# For each keyword that appears on grants by different PIs, create edges
kw_pi = {}  # kw → set of PIs
for r in active:
    pi = r.get('ResponsibleApplicantName','').strip()
    if not pi: continue
    for kw in r.get('Keywords','').split(','):
        kw = kw.strip().lower()
        if kw:
            kw_pi.setdefault(kw, set()).add(pi)

topic_co_edges = Counter()
for kw, pis in kw_pi.items():
    pis = list(pis)
    if len(pis) >= 2 and len(pis) <= 50:  # avoid huge generic keyword clusters
        for i in range(len(pis)):
            for j in range(i+1, len(pis)):
                a,b = sorted([pis[i], pis[j]])
                topic_co_edges[(a,b)] += 1

# Merge both edge sets
all_pi_edges = Counter(sin_pi_edges)
for k, v in topic_co_edges.items():
    all_pi_edges[k] += v

pipi = f'{REPORT_PATH}/network_pi_pi_edges.csv'
with open(pipi,'w',newline='',encoding='utf-8') as f:
    w=csv.writer(f); w.writerow(['source_pi','source_inst','target_pi','target_inst','weight'])
    for (a,b), wgt in sorted(all_pi_edges.items(), key=lambda x:-x[1]):
        w.writerow([a, pi_inst.get(a,''), b, pi_inst.get(b,''), wgt])
print(f"Written: {pipi} ({len(all_pi_edges)} edges)")

# Also write a Topic Co-PI summary (top keyword co-authorships)
top_kw_edges = f'{REPORT_PATH}/network_keyword_copipi.csv'
kw_edge_rows = []
for kw, pis in sorted(kw_pi.items(), key=lambda x: -len(x[1])):
    pis = list(pis)
    if 2 <= len(pis) <= 30:
        for i in range(len(pis)):
            for j in range(i+1, len(pis)):
                a,b = sorted([pis[i], pis[j]])
                kw_edge_rows.append({'keyword':kw,'pi_a':a,'pi_b':b,'inst_a':pi_inst.get(a,''),'inst_b':pi_inst.get(b,'')})
kw_edge_rows.sort(key=lambda x: -len([r for r in kw_edge_rows if r['keyword']==x['keyword']]))
with open(top_kw_edges,'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=['keyword','pi_a','pi_b','inst_a','inst_b'])
    w.writeheader(); w.writerows(kw_edge_rows)
print(f"Written: {top_kw_edges}")

# ── Sinergia dropdown ───────────────────────────────────────────
sinergia = sorted(
    [r for r in active if r.get('FundingInstrumentPublished','') == 'Sinergia'],
    key=lambda x: -float(x.get('AmountGrantedAllSets','0').replace(',','') or 0)
)
sin_csv = f'{REPORT_PATH}/sinergia_grants_dropdown.csv'
with open(sin_csv,'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=[
        'label','grant_id','title','pi','institution','amount_chf',
        'start_year','end_year','keywords','discipline','url'
    ],extrasaction='ignore')
    w.writeheader()
    for r in sinergia:
        gid = r.get('GrantNumberString','').strip()
        title = (r.get('TitleEnglish','') or r.get('Title','')).strip()[:120]
        label = (f"[Sinergia | CHF "
                 f"{float(r.get('AmountGrantedAllSets','0').replace(',','') or 0):,.0f}] "
                 f"{title[:80]}")
        w.writerow({**r,'label':label,'title':title,
                    'url':f'https://data.snf.ch/grants/{gid}'})
print(f"Written: {sin_csv} ({len(sinergia)} entries)")

# ── Summary stats ───────────────────────────────────────────────
print()
print(f"Active DH grants  : {len(active)}")
print(f"Sinergia (DH)     : {len(sinergia)}")
print(f"Co-PI edges       : {len(all_pi_edges)}")
print(f"PI-Pairs (topic)  : {len(topic_co_edges)}")
print(f"PI-Pairs (Sinergia): {len(sin_pi_edges)}")
print(f"Top-10 emerging   : {[kw for kw,_ in sup[:10]]}")