import csv, urllib.request, re, json
from collections import Counter, defaultdict
import itertools

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
dh_all  = [r for r in all_rows if is_dh_row(r)]
dh_active = [r for r in dh_all if r.get('State','').strip() in ('Ongoing','Approved')]

BASE = '/home/dh/repos/internal-reporting/reports/v0.1_dh-field-overview-2026-06'

# ───────────────────────────────────────────────────────────────────────
# NETWORK DATA
# ───────────────────────────────────────────────────────────────────────
# The SNSF dataset does NOT contain co-PI fields. We approximate
# collaborative networks via: shared keywords + shared institution + same
# instrument/year cluster. This gives a "topic+institutional co-occurrence"
# network rather than a true co-PI network (which would require the full
# Sinergia/Weave team data not available here).
#
# True co-PI edges can be inferred only for Sinergia/Weave grants —
# for these, we create instrument-level nodes (treat as collaborations).

TOPIC_KEYWORDS = {
    'AI/ML Methods':        ['machine learning','deep learning','artificial intelligence','neural networks',
                              'llm','large language models','generative ai','optimization'],
    'NLP & Text':           ['natural language processing','text mining','corpus linguistics',
                              'historical linguistics','computational linguistics','nlp'],
    'Computer Vision':      ['computer vision','image processing','image analysis'],
    'Digital Archives':     ['digital archives','digital edition','tei xml','text-encoding',
                              'digitised edition','digitized edition'],
    'Cultural Heritage':    ['digital cultural heritage','virtual reconstruction','3d model',
                              'digital heritage','heritage computing','digital memory'],
    'Digital Archaeology':  ['digital archaeology','remote sensing','archaeology','paleographic',
                              'epigraphic','codicology','virtual reconstruct'],
    'Computational History':['computational history','global history','environmental history','oral history'],
    'Media & Comm':         ['social media','communication sciences','media studies','digital media'],
    'Language Resources':   ['language resource','corpus building','parallel corpus','lexicography',
                              'lexicographic','corpus-based'],
    'Music & Art':          ['music','art','visual arts','digital music','computational music'],
}

# Build topic assignments
def assign_topics(keywords_str):
    kw_lower = keywords_str.lower()
    topics = []
    for topic, kws in TOPIC_KEYWORDS.items():
        if any(k in kw_lower for k in kws):
            topics.append(topic)
    return topics if topics else ['Other DH']

# Edge builder: PI ↔ Topic (for network)
pi_topic_edges = []   # (pi, topic, grant_id) — one row per pi-topic per grant
pi_inst_rows   = {}   # pi → institution (most frequent)

for r in dh_active:
    pi   = r.get('ResponsibleApplicantName','').strip()
    inst = r.get('ResearchInstitution','').strip()
    grant = r.get('GrantNumberString','').strip()
    topics = assign_topics(r.get('Keywords',''))
    if pi:
        if pi not in pi_inst_rows:
            pi_inst_rows[pi] = []
        pi_inst_rows[pi].append(inst)
    for t in topics:
        pi_topic_edges.append({'pi': pi, 'topic': t, 'grant': grant,
                                'institution': inst,
                                'discipline': r.get('MainDiscipline',''),
                                'amount': r.get('AmountGrantedAllSets',''),
                                'instrument': r.get('FundingInstrumentPublished','')})

# PI primary institution
pi_primary_inst = {}
for pi, insts in pi_inst_rows.items():
    pi_primary_inst[pi] = Counter(insts).most_common(1)[0][0]

# Node lists for network
pi_nodes = sorted(set(e['pi'] for e in pi_topic_edges if e['pi']))
topic_nodes = sorted(set(TOPIC_KEYWORDS.keys()))
inst_nodes  = sorted(set(pi_primary_inst.values()))

# PI ↔ Topic edges (weighted by number of grants)
pi_topic_counter = Counter((e['pi'], e['topic']) for e in pi_topic_edges if e['pi'])
# PI ↔ Institution edges (one edge per PI to their primary institution)

# Write PI → topic edge list
edge_out = f'{BASE}/data/network_pi_topic_edges.csv'
with open(edge_out, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['source_type','source','target_type','target','weight','sample_grant'])
    for (pi, topic), cnt in sorted(pi_topic_counter.items(), key=lambda x: -x[1]):
        sample = next((e['grant'] for e in pi_topic_edges if e['pi']==pi and e['topic']==topic), '')
        w.writerow(['pi', pi, 'topic', topic, cnt, sample])
print(f"Written: {edge_out}")

# Write node index
node_out = f'{BASE}/data/network_nodes.csv'
with open(node_out, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['node_type','node_id','label','institution','dh_grant_count'])
    for pi in pi_nodes:
        cnt = sum(1 for r in dh_active
                  if r.get('ResponsibleApplicantName','').strip() == pi)
        w.writerow(['person', pi, pi, pi_primary_inst.get(pi,''), cnt])
    for t in topic_nodes:
        cnt = sum(1 for e in pi_topic_edges if e['topic']==t)
        w.writerow(['topic', t, t, '', cnt])
print(f"Written: {node_out}")

# Institution co-occurrence via shared topics (institutional collaboration proxy)
inst_topic_counter = Counter()
for e in pi_topic_edges:
    if e['pi'] and e['topic'] and e['institution']:
        inst_topic_counter[(e['institution'], e['topic'])] += 1

inst_edge_out = f'{BASE}/data/network_inst_topic_edges.csv'
with open(inst_edge_out, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['source_type','source','target_type','target','weight'])
    for (inst, topic), cnt in sorted(inst_topic_counter.items(), key=lambda x: -x[1])[:200]:
        w.writerow(['institution', inst, 'topic', topic, cnt])
print(f"Written: {inst_edge_out}")

# ───────────────────────────────────────────────────────────────────────
# MAJOR GRANTS DROPDOWN LIST
# ───────────────────────────────────────────────────────────────────────
major_instruments = {
    'Sinergia','Weave/Lead Agency','Ambizione',
    'Eccellenza','Eccellenza grant','PRIMA','SPIRIT'
}

major = sorted(
    [r for r in dh_active
     if r.get('FundingInstrumentPublished','') in major_instruments
     or (float(r.get('AmountGrantedAllSets','0').replace(',','') or 0) >= 500000
         and r.get('FundingInstrumentPublished','') in {
             'Project funding','SNSF Advanced Grants','SNSF Starting Grants',
             'SNSF Consolidator Grants','Bridge - Proof of Concept'
         })],
    key=lambda x: -float(x.get('AmountGrantedAllSets','0').replace(',','') or 0)
)

dropdown_out = f'{BASE}/data/major-grants-dropdown.csv'
with open(dropdown_out, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=[
        'label','grant_id','title','pi','institution','instrument',
        'amount_chf','start_year','end_year','keywords','discipline','url'
    ], extrasaction='ignore')
    w.writeheader()
    for r in major:
        grant_id = r.get('GrantNumberString','').strip()
        title = (r.get('TitleEnglish','') or r.get('Title',''))[:120]
        label = f"[{r.get('FundingInstrumentPublished','')} | CHF {float(r.get('AmountGrantedAllSets','0') or 0):,.0f}] {title[:80]}"
        start = r.get('EffectiveGrantStartDate','')[:4]
        end   = r.get('EffectiveGrantEndDate','')[:4]
        url   = f"https://data.snf.ch/grants/{grant_id}"
        w.writerow({
            'label': label,
            'grant_id': grant_id,
            'title': title,
            'pi': r.get('ResponsibleApplicantName',''),
            'institution': r.get('ResearchInstitution',''),
            'instrument': r.get('FundingInstrumentPublished',''),
            'amount_chf': r.get('AmountGrantedAllSets',''),
            'start_year': start,
            'end_year': end,
            'keywords': r.get('Keywords',''),
            'discipline': r.get('MainDiscipline',''),
            'url': url
        })
print(f"Written: {dropdown_out} ({len(major)} entries)")

# Also write as JSON for programmatic use
dropdown_json = f'{BASE}/data/major-grants-dropdown.json'
with open(dropdown_json, 'w', encoding='utf-8') as f:
    json.dump([{
        'label': f"[{r.get('FundingInstrumentPublished','')} | CHF {float(r.get('AmountGrantedAllSets','0') or 0):,.0f}] "
                 f"{(r.get('TitleEnglish','') or r.get('Title',''))[:80]}",
        'grant_id': r.get('GrantNumberString','').strip(),
        'title': (r.get('TitleEnglish','') or r.get('Title',''))[:120],
        'pi': r.get('ResponsibleApplicantName',''),
        'institution': r.get('ResearchInstitution',''),
        'instrument': r.get('FundingInstrumentPublished',''),
        'amount_chf': float(r.get('AmountGrantedAllSets','0').replace(',','') or 0),
        'start_year': r.get('EffectiveGrantStartDate','')[:4],
        'end_year': r.get('EffectiveGrantEndDate','')[:4],
        'keywords': r.get('Keywords',''),
        'discipline': r.get('MainDiscipline',''),
        'url': f"https://data.snf.ch/grants/{r.get('GrantNumberString','').strip()}"
    } for r in major], f, ensure_ascii=False, indent=2)
print(f"Written: {dropdown_json}")

# ───────────────────────────────────────────────────────────────────────
# EXECUTIVE SUMMARY DATA
# ───────────────────────────────────────────────────────────────────────
# Collect numbers for exec summary
total_active = len(dh_active)
total_amt = sum(float(r.get('AmountGrantedAllSets','0').replace(',','') or 0) for r in dh_active)

ongoing_ct  = sum(1 for r in dh_active if r.get('State')=='Ongoing')
approved_ct = sum(1 for r in dh_active if r.get('State')=='Approved')

fi_ct  = Counter(r.get('FundingInstrumentPublished','') for r in dh_active)
inst_ct = Counter(r.get('ResearchInstitution','') for r in dh_active)
disc_ct = Counter(r.get('MainDiscipline','') for r in dh_active)
country_ct = Counter(r.get('InstituteCountry','') for r in dh_active)

mobility_pct = 100 * sum(v for k,v in fi_ct.items() if 'obility' in k or 'xchange' in k or 'Doc' in k) / total_active

print(f"Active DH grants: {total_active}")
print(f"Total funding: CHF {total_amt:,.0f}")
print(f"Ongoing: {ongoing_ct}, Approved: {approved_ct}")
print(f"Top disciplines: {disc_ct.most_common(5)}")
print(f"Top institutions: {inst_ct.most_common(5)}")
print(f"Mobility/exchange share: {mobility_pct:.1f}%")
print("Done.")