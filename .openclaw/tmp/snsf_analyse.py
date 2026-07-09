import csv, urllib.request, re
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
    'Library and documentation science / Archive science',
}

DH_PATTERNS = [
    'digital human', ' DH ', 'digitis', 'digitaliz',
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
total = len(all_rows)
dh_all = [r for r in all_rows if is_dh_row(r)]
dh_active = [r for r in dh_all if r.get('State','').strip() in ('Ongoing', 'Approved')]

print(f"Total grants:           {total}")
print(f"DH grants (all):        {len(dh_all)} ({100*len(dh_all)/total:.1f}%)")
print(f"DH grants (active):     {len(dh_active)}")
print(f"  Ongoing:              {sum(1 for r in dh_active if r.get('State')=='Ongoing')}")
print(f"  Approved:             {sum(1 for r in dh_active if r.get('State')=='Approved')}")
print()

# Overview stats
disc_c = Counter(r.get('MainDiscipline','').strip() for r in dh_active)
print("By discipline:")
for k, v in disc_c.most_common(20):
    print(f"  {v:>4}  {k}")
print()

inst_c = Counter(r.get('ResearchInstitution','').strip() for r in dh_active)
print("By institution (top 20):")
for k, v in inst_c.most_common(20):
    print(f"  {v:>4}  {k}")
print()

fi_c = Counter(r.get('FundingInstrumentPublished','').strip() for r in dh_active)
print("By funding instrument:")
for k, v in fi_c.most_common():
    print(f"  {v:>4}  {k}")
print()

country_c = Counter(r.get('InstituteCountry','').strip() for r in dh_active)
print("By country:")
for k, v in country_c.most_common(10):
    print(f"  {v:>4}  {k}")
print()

total_amt = 0
for r in dh_active:
    try:
        total_amt += float(r.get('AmountGrantedAllSets','0').replace(',','').strip() or 0)
    except:
        pass
print(f"Total funding volume (active DH grants): CHF {total_amt:,.0f}")
print()

pi_c = Counter(r.get('ResponsibleApplicantName','').strip() for r in dh_active)
print("Top 20 PIs (by number of active DH grants):")
for k, v in pi_c.most_common(20):
    print(f"  {v:>4}  {k}")
print()

kw_counter = Counter()
for r in dh_active:
    kws = r.get('Keywords','').split(',')
    for kw in kws:
        kw = kw.strip()
        if kw and len(kw) > 2:
            kw_counter[kw.lower()] += 1
print("Top 40 keywords:")
for k, v in kw_counter.most_common(40):
    print(f"  {v:>4}  {k}")