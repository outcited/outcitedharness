#!/usr/bin/env python3
"""Container-children facet extractor v1 — non-PDF children of unpacked archives.

Deterministic, no LLM: classify by extension family, extract quick facts
(gerber layer/aperture counts, drill hits, SPICE model names, LTspice pins,
sidecar text heads), emit one jsonl row per child. Index pass — "figure out
what we have" per Sam's 2026-10-20 directive.
"""
import json, os, sys, hashlib
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

CHILDREN = Path('/Users/samkim/Harnessv1/results/containers-20261012/children')
OUT = Path('/Users/samkim/lane-out/children-facets.jsonl')

FAMILIES = {
    'gerber': {'.gbr','.gtl','.gbl','.gto','.gbo','.gts','.gbs','.gml','.gtp','.gbp',
               '.gp1','.gp2','.gp3','.gm1','.gko','.pho','.art','.gpi'},
    'drill': {'.drl','.dri','.exc'},
    'spice': {'.lib','.cir','.spi','.mod','.sub'},
    'ltspice_symbol': {'.asy'},
    'orcad': {'.olb','.sxslb','.sxcmp','.dsn'},
    'fab_report': {'.rep','.lst','.apr','.rul'},
    'sidecar_text': {'.txt','.md','.log','.csv'},
    'image': {'.png','.jpg','.gif','.bmp','.svg'},
    'pdf': {'.pdf'},
    'eda_model': {'.s2p','.s4p','.sp4','.sin'},
}

def fam(ext):
    for f, exts in FAMILIES.items():
        if ext in exts: return f
    return 'other'

def gerber_facts(p):
    facts = {}
    try:
        head = tail = b''
        with open(p,'rb') as fh:
            head = fh.read(4096)
            sz = os.fstat(fh.fileno()).st_size
            if sz > 8192:
                fh.seek(-4096, 2); tail = fh.read(4096)
        txt = (head+tail).decode('latin-1', 'replace')
        facts['apertures'] = txt.count('\nD')
        facts['coords'] = txt.count('D0')
        for line in txt.splitlines():
            if line.startswith('G04') or line.startswith('%FSLA'):
                facts.setdefault('header', line[:80])
                break
        facts['size_kb'] = round(sz/1024, 1)
    except Exception as e:
        facts['err'] = str(e)[:80]
    return facts

def drill_facts(p):
    facts = {}
    try:
        with open(p,'rb') as fh: data = fh.read(2_000_000)
        txt = data.decode('latin-1','replace')
        facts['tools'] = txt.count('\nT')
        hits = 0
        import re
        for m in re.finditer(r'^T(\d+)', txt, re.M): pass
        facts['size_kb'] = round(len(data)/1024, 1)
    except Exception as e:
        facts['err'] = str(e)[:80]
    return facts

def spice_facts(p):
    facts = {'models': []}
    try:
        with open(p,'rb') as fh:
            for i, line in enumerate(fh):
                if i > 3000: break
                t = line.decode('latin-1','replace').strip()
                if t[:1] == '*': facts.setdefault('title', t[:100])
                tl = t.lower()
                if tl.startswith('.model ') or tl.startswith('.subckt '):
                    parts = t.split()
                    if len(parts) > 1 and len(facts['models']) < 25:
                        facts['models'].append(' '.join(parts[:3])[:60])
    except Exception as e:
        facts['err'] = str(e)[:80]
    return facts

def asy_facts(p):
    facts = {}
    try:
        txt = open(p,'rb').read(200_000).decode('latin-1','replace')
        facts['pins'] = txt.lower().count('pinattr')
        facts['window_lines'] = txt.count('WINDOW')
    except Exception as e:
        facts['err'] = str(e)[:80]
    return facts

def text_facts(p):
    facts = {}
    try:
        head = open(p,'rb').read(300).decode('utf-8','replace')
        facts['head'] = ' '.join(head.split())[:150]
    except Exception as e:
        facts['err'] = str(e)[:80]
    return facts

def one(p):
    ext = os.path.splitext(p)[1].lower()
    f = fam(ext)
    row = {'source_path': p, 'family': f, 'ext': ext or None,
           'size': os.path.getsize(p)}
    if f == 'gerber': row['facts'] = gerber_facts(p)
    elif f == 'drill': row['facts'] = drill_facts(p)
    elif f == 'spice': row['facts'] = spice_facts(p)
    elif f == 'ltspice_symbol': row['facts'] = asy_facts(p)
    elif f in ('sidecar_text','fab_report'): row['facts'] = text_facts(p)
    return row

def main():
    paths = []
    for root, dirs, files in os.walk(CHILDREN):
        for fn in files:
            if fn.startswith('._'): continue
            paths.append(os.path.join(root, fn))
    print(f"children: {len(paths)}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(OUT, 'w') as fh, ProcessPoolExecutor(max_workers=8) as ex:
        for row in ex.map(one, paths, chunksize=64):
            fh.write(json.dumps(row, sort_keys=True) + "\n"); n += 1
    print("wrote", n, "->", OUT, flush=True)

if __name__ == '__main__':
    main()
