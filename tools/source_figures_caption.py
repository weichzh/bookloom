"""Frozen caption barrier: PF2 identity + MuPDF native line/glyph geometry.

Never reads reference/scoring files. Inputs and outputs are scratch-only.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import re
import time
import unicodedata
import xml.etree.ElementTree as ET


def norm(s):
    return ''.join(c for c in unicodedata.normalize('NFKC',s).casefold() if c.isalnum())


_CAPTION_PREFIX = re.compile(
    r'^\s*(?P<kind>figure|fig|table|tab)\.?\s*'
    r'(?P<number>[a-z]?\d+(?:[.\-–—]\d+)*(?:[a-z])?)(?!\w|[.\-–—]\d)', re.IGNORECASE)
_CAPTION_NUMBER = re.compile(r'[a-z]?\d+(?:[.\-–—]\d+)*(?:[a-z])?\Z', re.IGNORECASE)


def caption_type(value):
    value = unicodedata.normalize('NFKC', str(value)).casefold().strip().rstrip('.')
    return 'figure' if value in ('fig', 'figure') else 'table' if value in ('tab', 'table') else None


def caption_number(value):
    value = unicodedata.normalize('NFKC', str(value)).casefold().strip().rstrip('.')
    return value if _CAPTION_NUMBER.fullmatch(value) else None


def prefix_identity(value):
    value = unicodedata.normalize('NFKC', str(value)).casefold()
    match = _CAPTION_PREFIX.match(value)
    if not match:
        return None
    return (caption_type(match.group('kind')), caption_number(match.group('number')), match.end())


def matching_text(value):
    value = unicodedata.normalize('NFKC', str(value)).casefold()
    parsed = prefix_identity(value)
    if parsed:
        return parsed[0] + norm(parsed[1]) + norm(value[parsed[2]:])
    return norm(value)


def valid_xml(c):
    v = ord(c)
    return v in (9,10,13) or 0x20 <= v <= 0xD7FF or 0xE000 <= v <= 0xFFFD or 0x10000 <= v <= 0x10FFFF

def parse_xml(path):
    data = path.read_text(encoding='utf8')
    count = sum(not valid_xml(c) for c in data)
    data = ''.join(c for c in data if valid_xml(c))
    # XML numeric references can encode characters forbidden in XML 1.0.
    def replace(match):
        nonlocal count
        n = int(match.group(2), 16 if match.group(1) else 10)
        if n > 0x10ffff or not valid_xml(chr(n)):
            count += 1
            return ''
        return match.group()
    data = re.sub(r'&#(x?)([0-9a-fA-F]+);',replace,data)
    return ET.fromstring(data),count

def bbox_quad(value):
    p = [float(x) for x in value.split()]
    if len(p) != 8 or not all(math.isfinite(x) for x in p):
        raise ValueError('missing or invalid source glyph quad')
    return [min(p[::2]),min(p[1::2]),max(p[::2]),max(p[1::2])],p

def union(boxes):
    return [min(b[0] for b in boxes),min(b[1] for b in boxes),
            max(b[2] for b in boxes),max(b[3] for b in boxes)]

def load_lines(path,md):
    root,removed = parse_xml(path)
    pages = root.findall('page')
    if len(pages)!=1 or md['rotation_deg']!=0:
        raise ValueError('unsupported text page or rotation')
    p = pages[0]
    crop=md['cropbox_pt']; width=crop[2]-crop[0]; height=crop[3]-crop[1]
    if abs(float(p.attrib['width'])-width)>0.02 or abs(float(p.attrib['height'])-height)>0.02:
        raise ValueError('source text page dimensions do not match CropBox')
    if md['mutool_display_pt_to_pixel_affine'] != [225/72,0,0,225/72,0,0]:
        raise ValueError('unsupported source-text display affine')
    blocks=[]
    for bi,block in enumerate(p.findall('block')):
        lines=[]
        for li,line in enumerate(block.findall('line')):
            chars=[]; chartext=[]; error=None
            for c in line.iter('char'):
                val=c.get('c',''); chartext.append(val)
                if not val or val.isspace():
                    continue
                try:
                    box,quad=bbox_quad(c.attrib.get('quad',''))
                except ValueError as ex:
                    error=str(ex); continue
                chars.append({'text':val,'quad_pt':quad,'bbox_pt':box})
            text=''.join(chartext)
            lines.append({'id':f'block-{bi}:line-{li}','text':text,'normalized':norm(text),
                          'glyphs':chars,'error':error})
        if lines: blocks.append(lines)
    return blocks,removed

def edges_distance(a,b):
    dx=max(0,a[0]-b[2],b[0]-a[2]); dy=max(0,a[1]-b[3],b[1]-a[3])
    return math.hypot(dx,dy)

def cap_raw(raw,page):
    found=[]
    for item in raw['figures']:
        if item['page']==page-1:
            found.append(('figures',item,item['caption'],item['captionBoundary']))
    for item in raw['regionless-captions']:
        if item['page']==page-1:
            found.append(('regionless-captions',item,item['text'],item['boundary']))
    return found

def align_caption(entry,blocks):
    origin,item,text,boundary=entry
    ident=norm(text); name=str(item.get('name','')); typ=str(item.get('figType',''))
    parsed=prefix_identity(text)
    expected_type=caption_type(typ) or (parsed[0] if parsed else None)
    expected_number=caption_number(name) or (parsed[1] if parsed else None)
    if (not expected_type or not expected_number or
            parsed and (parsed[0] != expected_type or parsed[1] != expected_number)):
        return {'status':'unavailable','reason':'caption type or number unavailable or inconsistent',
                'origin':origin,'predictor':item}
    variants={v for v in (matching_text(text), matching_text(typ+' '+name+' '+text),
                          matching_text(name+' '+text)) if v}
    b=[float(boundary[k]) for k in ('x1','y1','x2','y2')]
    if not variants or not all(math.isfinite(v) for v in b) or b[0]>=b[2] or b[1]>=b[3]:
        return {'status':'unavailable','reason':'invalid PF2 text or boundary','origin':origin,'predictor':item}
    matches=[]
    for block in blocks:
        for i in range(len(block)):
            for j in range(i+1,len(block)+1):
                chunk=block[i:j]
                candidate_text='\n'.join(x['text'] for x in chunk)
                source_identity=prefix_identity(candidate_text)
                if not source_identity or source_identity[:2] != (expected_type,expected_number):
                    continue
                candidate=matching_text(candidate_text)
                if candidate not in variants:
                    continue
                glyphs=[g for line in chunk for g in line['glyphs']]
                if not glyphs or any(line['error'] for line in chunk):
                    return {'status':'unavailable','reason':'matching line has missing or invalid glyph quad',
                            'origin':origin,'predictor':item}
                box=union([g['bbox_pt'] for g in glyphs])
                if edges_distance(box,b)<=5:
                    matches.append({'line_ids':[x['id'] for x in chunk],
                                    'lines':[x['text'] for x in chunk],
                                    'text':'\n'.join(x['text'] for x in chunk),
                                    'normalized':candidate,'source_snap_bbox_pt':box,
                                    'glyphs':glyphs,'pf2_edge_distance_pt':edges_distance(box,b)})
    result={'origin':origin,'predictor':item,'pf2_boundary_pt':b,'normalized_pf2':ident,
            'caption_type':expected_type,'caption_number':expected_number,
            'matching_span_count':len(matches)}
    if len(matches)!=1:
        result.update(status='ambiguous' if len(matches)>1 else 'unavailable',
                      reason='multiple matching line spans' if matches else 'no matching line span')
    else:
        result.update(status='ok',source_snap=matches[0])
    return result

def overlap(a,b):
    return min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1])

def direction(c,b,w,h):
    sep=[]
    if c[2]<b[0]: sep.append(('left',(b[0]-c[2])/w))
    if c[0]>b[2]: sep.append(('right',(c[0]-b[2])/w))
    if c[3]<b[1]: sep.append(('above',(b[1]-c[3])/h))
    if c[1]>b[3]: sep.append(('below',(c[1]-b[3])/h))
    sep.sort(key=lambda x:x[1],reverse=True)
    if not sep or len(sep)>1 and sep[0][1]==sep[1][1]:
        return None,sep
    return sep[0][0],sep

def barrier(panel,continuous,b,caps,w,h):
    crop=list(panel); moves=[]
    for cap in caps:
        c=cap['source_snap']['bbox_px']
        if not overlap(continuous,c): continue
        d,seps=direction(c,b,w,h)
        if d is None:
            return None,moves,'caption has no unique strictly separated direction'
        moves.append({'direction':d,'caption_bbox_px':c,'source_line_ids':cap['source_snap']['line_ids'],
                      'separations_normalized':seps})
    if len({x['direction'] for x in moves})>1:
        return None,moves,'inconsistent directions across captions'
    for m in moves:
        c=m['caption_bbox_px']; d=m['direction']
        if d=='below': crop[3]=min(crop[3],math.floor(c[1])-1)
        elif d=='above': crop[1]=max(crop[1],math.ceil(c[3])+1)
        elif d=='right': crop[2]=min(crop[2],math.floor(c[0])-1)
        else: crop[0]=max(crop[0],math.ceil(c[2])+1)
    crop=[max(0,min(w,crop[0])),max(0,min(h,crop[1])),max(0,min(w,crop[2])),max(0,min(h,crop[3]))]
    cx=(b[0]+b[2])/2; cy=(b[1]+b[3])/2
    if not (crop[0]<cx<crop[2] and crop[1]<cy<crop[3]):
        return None,moves,'barrier removes B center or yields empty panel'
    return crop,moves,None

def ink_bbox(image,box):
    import numpy as np
    x0,y0,x1,y1=box
    a=np.asarray(image.crop((x0,y0,x1,y1)).convert('L'))
    ys,xs=np.nonzero(a<239)
    return [int(x0+xs.min()),int(y0+ys.min()),int(x0+xs.max()+1),int(y0+ys.max()+1)] if len(xs) else None
