import csv, urllib.request, re, json, sys
from collections import Counter

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
    text = f"{row.get('Keywords','')} {row.get('Abstract','')} {row.get('Title','')} {row.get('TitleEnglish','')}".lower()
    if disc in DH_DISCIPLINES:
        return True
    for p in compiled_patterns:
        if p.search(text):
            return True
    return False

all_rows = list(reader)
print(f"Total rows: {len(all_rows)}")

dh_all = [r for r in all_rows if is_dh_row(r)]
dh_active = [r for r in dh_all if r.get('State','').strip() in ('Ongoing','Approved')]

print(f"DH all: {len(dh_all)}, DH active: {len(dh_active)}")

# Key columns to export
KEY_COLS = [
    'GrantNumber', 'GrantNumberString', 'Title', 'TitleEnglish',
    'ResponsibleApplicantName', 'ResearchInstitution', 'InstituteCountry',
    'FundingInstrumentPublished', 'MainDiscipline', 'AllDisciplines',
    'MainFieldOfResearch', 'EffectiveGrantStartDate', 'EffectiveGrantEndDate',
    'AmountGrantedAllSets', 'Keywords', 'Abstract', 'State',
    'CallFullTitle', 'CallDecisionYear'
]

# 1. active-dh-grants.csv
out_path = '/home/dh/repos/internal-reporting/reports/v0.1_dh-field-overview-2026-06/data/active-dh-grants.csv'
with open(out_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=KEY_COLS, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(dh_active)
print(f"Written: {out_path}")

# 2. dh-keyword-frequency.csv
kw_counter = Counter()
for r in dh_active:
    for kw in r.get('Keywords','').split(','):
        kw = kw.strip().lower()
        if kw and len(kw) > 2:
            kw_counter[kw] += 1
kw_path = '/home/dh/repos/internal-reporting/reports/v0.1_dh-field-overview-2026-06/data/dh-keyword-frequency.csv'
with open(kw_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['keyword','count'])
    for kw, cnt in kw_counter.most_common(200):
        writer.writerow([kw, cnt])
print(f"Written: {kw_path}")

# 3. institutional-breakdown.csv
inst_rows = []
inst_counter = Counter(r.get('ResearchInstitution','').strip() for r in dh_active)
for inst, cnt in inst_counter.most_common():
    inst_rows.append({'institution': inst, 'dh_grant_count': cnt})
inst_path = '/home/dh/repos/internal-reporting/reports/v0.1_dh-field-overview-2026-06/data/institutional-breakdown.csv'
with open(inst_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=['institution','dh_grant_count'])
    writer.writeheader()
    writer.writerows(inst_rows)
print(f"Written: {inst_path}")

# 4. dh-pi-index.csv (2+ grants)
pi_counter = Counter()
for r in dh_active:
    name = r.get('ResponsibleApplicantName','').strip()
    if name:
        pi_counter[name] += 1
pi_path = '/home/dh/repos/internal-reporting/reports/v0.1_dh-field-overview-2026-06/data/dh-pi-index.csv'
with open(pi_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['pi_name','active_dh_grants'])
    for name, cnt in sorted(pi_counter.items(), key=lambda x: -x[1]):
        if cnt >= 2:
            writer.writerow([name, cnt])
print(f"Written: {pi_path}")

print("Done.")