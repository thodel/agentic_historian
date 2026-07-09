#!/usr/bin/env python3
from datetime import datetime, timezone
import feedparser
import re
import os
import glob

CUTOFF = datetime(2026, 6, 28, tzinfo=timezone.utc)
NOW = datetime(2026, 7, 5, 14, 0, tzinfo=timezone.utc)

FILE_FEED_MAP = {
    "06b475e3": "Reviews in DH",
    "1021a6b8": "Transformations (DARIAH)",
    "20b85546": "Zeitschrift für Digital Humanities",
    "4498bc5d": "Digital Humanities Now",
    "567a752f": "arXiv cs.DL",
    "6ae5781b": "Journal of Data Mining & Digital Humanities",
    "6be776e5": "Google Alert (Digital Studies)",
    "6f8acf95": "Google Alert (DHQ)",
    "7aa75dfe": "Google Alert (Open Humanities Data)",
    "970c807f": "Google Alert (magazén)",
    "982d1f98": "JDMDH",
    "a8c03a25": "Transformations (DARIAH)",
    "b3d41143": "Springer Journal of Digital History",
    "bd503ede": "Code4Lib Journal",
    "dc72b68e": "Critical Digital Humanities",
    "e99e20d8": "Histoire & mesure",
    "f3d5ee1e": "Acta historica et archaeologica mediaevalia",
}

RELEVANCE_KEYWORDS = [
    "manuscript", "HTR", "OCR", "kraken", "escriptorium",
    "medieval", "early modern",
    "paleography", "philology",
    "named entity", "entity extraction", "entity linking",
    "knowledge graph", "ontology",
    "prosopography",
    "TEI", "digital edition", "CIDOC-CRM",
    "FAIR", "data workflow",
    "konigsfelden",
    "transcription", "text recognition",
    "archival", "charter", "diplomatic",
]

HIGH_VALUE = ["kraken", "escriptorium", "HTR", "tei", "CIDOC-CRM", 
              "prosopography", "knowledge graph", "named entity",
              "digital edition", "paleography", "medieval", "manuscript"]

VERY_HIGH = ["kraken", "escriptorium", "HTR", "tei", "CIDOC-CRM", 
             "prosopography", "knowledge graph", "konigsfelden"]

def score_relevance(title, description, authors=""):
    text = f"{title} {description} {authors}".lower()
    text = text.replace('ö', 'o').replace('ä', 'a').replace('ü', 'u')
    count = sum(1 for kw in HIGH_VALUE if kw in text)
    vh = sum(1 for kw in VERY_HIGH if kw in text)
    matches = sum(1 for kw in RELEVANCE_KEYWORDS if kw in text)
    if vh >= 2: return 5
    elif vh == 1 and count >= 2: return 5
    elif count >= 3: return 4
    elif count >= 1: return 3
    elif matches >= 2: return 2
    return 1

def extract_doi(title, description):
    m = re.search(r'10\.\d{4,}/[^\s\]\)>{}]+', f"{title} {description}")
    return m.group(0).rstrip('.,;)]} ') if m else None

def extract_arxiv_id(title, description):
    m = re.search(r'arXiv:\s*(\d+\.\d+)', f"{title} {description}")
    return m.group(1) if m else None

def clean_text(text, max_len=500):
    if not text: return ""
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&#039;', "'")
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_len] + ('...' if len(text) > max_len else '')

def parse_date(entry):
    for field in ['published', 'updated', 'dc:date']:
        val = entry.get(field)
        if val:
            try:
                from email.utils import parsedate_tz, mktime_tz
                t = parsedate_tz(val)
                if t: return datetime.fromtimestamp(mktime_tz(t), tz=timezone.utc)
            except: pass
    for attr in ['published_parsed', 'updated_parsed']:
        if hasattr(entry, attr):
            try:
                import time
                return datetime.fromtimestamp(time.mktime(getattr(entry, attr)), tz=timezone.utc)
            except: pass
    return None

feed_dir = ".openclaw/tmp/feeds"
all_articles = []

for fpath in sorted(glob.glob(f"{feed_dir}/*.xml")):
    fname = os.path.basename(fpath).replace('.xml', '')
    feed_name = FILE_FEED_MAP.get(fname, fname)
    feed = feedparser.parse(fpath)
    items_found = 0
    
    for entry in feed.entries[:50]:
        if items_found >= 5: break
        
        title = clean_text(entry.get('title', ''))
        if not title: continue
        
        description = clean_text(entry.get('description', '') or entry.get('summary', '') or '')
        link = entry.get('link', '') or entry.get('id', '')
        
        if hasattr(entry, 'author') and entry.author:
            authors = clean_text(entry.author)
        elif hasattr(entry, 'authors') and entry.authors:
            authors = '; '.join(clean_text(a.get('name', str(a))) for a in entry.authors)
        else:
            authors = ''
        
        dt = parse_date(entry)
        if dt and (dt < CUTOFF or dt > NOW): continue
        
        # arXiv keyword filter
        if 'arXiv' in feed_name or 'cs.DL' in feed_name or 'cs.IR' in feed_name:
            txt = f"{title} {description}".lower()
            if not any(kw in txt for kw in ['manuscript', 'ocr', 'htr', 'kraken', 'escriptorium', 'medieval', 'paleography', 'tei', 'digital edition', 'knowledge graph', 'named entity', 'prosopography', 'charter', 'philology']):
                continue
        
        doi = extract_doi(title, description)
        arxiv_id = extract_arxiv_id(title, description)
        relevance = score_relevance(title, description, authors)
        if relevance < 2: continue
        
        all_articles.append({
            'feed': feed_name,
            'title': title,
            'link': link,
            'doi': doi,
            'arxiv_id': arxiv_id,
            'relevance': relevance,
            'authors': authors,
            'description': description,
            'date': dt.isoformat() if dt else 'unknown',
        })
        items_found += 1

all_articles.sort(key=lambda x: -x['relevance'])
print(f"Total: {len(all_articles)}")
for a in all_articles:
    print(f"\n[Score {a['relevance']}] {a['feed']}")
    print(f"  Title: {a['title'][:100]}")
    print(f"  Authors: {a['authors'][:80]}")
    print(f"  Date: {a['date']}")
    print(f"  DOI: {a['doi']}")
    print(f"  arXiv: {a['arxiv_id']}")
    print(f"  Link: {a['link']}")
    print(f"  Desc: {a['description'][:200]}")