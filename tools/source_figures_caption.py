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
import statistics
import time
import unicodedata
import xml.etree.ElementTree as ET


def norm(s):
    return ''.join(c for c in unicodedata.normalize('NFKC',s).casefold() if c.isalnum())


_NUMBER_PATTERN = r'(?:[a-z]+[.\-–—]|[a-z]?)\d+(?:[.\-–—]\d+)*(?:[a-z])?'
_CAPTION_PREFIX = re.compile(
    r'^\s*(?P<kind>figure|fig|table|tab)\.?\s*'
    rf'(?P<number>{_NUMBER_PATTERN})(?!\w|[.\-–—]\d)', re.IGNORECASE)
_CAPTION_NUMBER = re.compile(rf'{_NUMBER_PATTERN}\Z', re.IGNORECASE)
_INLINE_LABEL = re.compile(
    r'(?<!\w)(?:figure|fig|table|tab)\.?\s*'
    rf'{_NUMBER_PATTERN}(?!\w|[.\-–—]\d)(?=\s*[:.\u2013\u2014])', re.IGNORECASE)


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
            for ci,c in enumerate(line.iter('char')):
                val=c.get('c',''); chartext.append(val)
                if not val:
                    error='missing source glyph text'; continue
                if val.isspace():
                    continue
                try:
                    box,quad=bbox_quad(c.attrib.get('quad',''))
                except ValueError as ex:
                    error=str(ex); continue
                chars.append({'id':f'block-{bi}:line-{li}:glyph-{ci}',
                              'text':val,'quad_pt':quad,'bbox_pt':box})
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


def _page_lines(blocks):
    """Validate the whole native page before using a spatial boundary.

    A glyph with unknown coordinates anywhere on the page could belong to the
    candidate.  In contrast to text matching, a boundary cannot place it safely.
    """
    lines=[]; ids=set()
    for block in blocks:
        for line in block:
            if line.get('error') or not line.get('id') or line['id'] in ids:
                return None
            ids.add(line.get('id'))
            glyphs=line.get('glyphs',[])
            visible=lambda value: ''.join(c for c in value if not c.isspace())
            if visible(line.get('text','')) != visible(''.join(g.get('text','') for g in glyphs)):
                return None
            if not glyphs:
                continue
            for glyph in glyphs:
                try:
                    box=[float(v) for v in glyph['bbox_pt']]
                    quad=[float(v) for v in glyph['quad_pt']]
                except (KeyError,TypeError,ValueError):
                    return None
                # MuPDF emits zero-advance characters for decomposed ligatures;
                # a zero-width or zero-height quad still has a known position.
                if (len(box)!=4 or len(quad)!=8 or not all(math.isfinite(v) for v in box+quad)
                        or box[0]>box[2] or box[1]>box[3]
                        or box[0]==box[2] and box[1]==box[3]):
                    return None
                actual=[min(quad[::2]),min(quad[1::2]),max(quad[::2]),max(quad[1::2])]
                if any(abs(a-b)>1e-7 for a,b in zip(box,actual)):
                    return None
            lines.append({'line':line, 'box':union([g['bbox_pt'] for g in glyphs]),
                          'order':len(lines)})
    return lines


def _vertical_overlap(a,b):
    return max(0,min(a[3],b[3])-max(a[1],b[1]))


def _same_band(a,b):
    return _vertical_overlap(a,b) >= 0.25*min(a[3]-a[1],b[3]-b[1])


def _visual_rows(lines):
    """Join nearby, overlapping-height fragments without bridging columns."""
    parents=list(range(len(lines)))
    def root(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]]; i=parents[i]
        return i
    ordered=sorted(range(len(lines)),key=lambda i:lines[i]['box'][1])
    for pos,i in enumerate(ordered):
        a=lines[i]['box']
        for j in ordered[pos+1:]:
            b=lines[j]['box']
            if b[1]>=a[3]:
                break
            gap=max(0,a[0]-b[2],b[0]-a[2])
            if _same_band(a,b) and gap<=1.5*max(a[3]-a[1],b[3]-b[1]):
                parents[root(j)]=root(i)
    groups={}
    for i,line in enumerate(lines):
        groups.setdefault(root(i),[]).append(line)
    rows=[]
    for fragments in groups.values():
        fragments.sort(key=lambda part:(part['box'][0],part['order']))
        rows.append({'fragments':fragments,'box':union([p['box'] for p in fragments]),
                     'text':''.join(p['line']['text'] for p in fragments)})
    return rows


def _has_label_separator(text, identity):
    text=unicodedata.normalize('NFKC',str(text)).casefold()
    parsed=prefix_identity(text)
    return bool(parsed and parsed[:2]==identity and text[parsed[2]:].lstrip().startswith((':','.','—','–')))


def _label_count(text):
    return len(_INLINE_LABEL.findall(unicodedata.normalize('NFKC',text)))


def _math_glyph_slot(char):
    category=unicodedata.category(char)
    if category=='Sm':
        return True
    # Combining Diacritical Marks for Symbols and named overlays are usable
    # as symbol slots; ordinary combining accents are not prose wildcards.
    return category.startswith('M') and (
        0x20d0<=ord(char)<=0x20ff or 'OVERLAY' in unicodedata.name(char,''))


def _compatible_pf2_text(native_glyphs, predictor):
    """Bounded glyph alignment, not a claim of equal extractor text.

    A PF2 question mark can stand for exactly one native glyph.  A native
    math symbol or combining overlay can be omitted or consume at most one
    PF2 character.  All other native letters and digits must match in order.
    The native glyph and its original quad are never changed.
    """
    units=[''.join(c for c in unicodedata.normalize('NFKC',str(g['text'])).casefold()
                   if not c.isspace()) for g in native_glyphs]
    native=''.join(units)
    predictor=unicodedata.normalize('NFKC',predictor).casefold()
    left=prefix_identity(native); right=prefix_identity(predictor)
    if not left or right and left[:2]!=right[:2]:
        return False
    remaining=left[2]
    body=[]
    for unit in units:
        skip=min(remaining,len(unit)); remaining-=skip
        unit=unit[skip:]
        tokens=[('exact',c) if c.isalnum() or c=='?' else ('math',c)
                for c in unit if c.isalnum() or c=='?' or _math_glyph_slot(c)]
        if tokens:
            body.append(tokens)
    predictor_body=predictor[right[2]:] if right else predictor
    other=[c for c in predictor_body if c.isalnum() or c=='?' or _math_glyph_slot(c)]

    # States are PF2 character positions.  The '?' branch consumes exactly
    # one native glyph even if NFKC expands that glyph to multiple letters.
    positions={0}
    for glyph in body:
        unknown={i+1 for i in positions if i<len(other) and other[i]=='?'}
        matched=positions
        for kind,char in glyph:
            if kind=='math':
                matched=matched | {i+1 for i in matched if i<len(other)}
            else:
                matched={i+1 for i in matched if i<len(other) and other[i]==char}
            if not matched:
                break
        positions=unknown | matched
        if not positions:
            return False
    return len(other) in positions


def _adjacent_line_may_continue(rows, selected):
    box=union([row['box'] for row in selected])
    line_height=statistics.median(row['box'][3]-row['box'][1] for row in selected)
    centers=sorted((r['box'][1]+r['box'][3])/2 for r in selected)
    steps=[b-a for a,b in zip(centers,centers[1:]) if b-a>0.5*line_height]
    pitch=statistics.median(steps) if steps else 1.4*line_height
    first,last=centers[0],centers[-1]
    for row in rows:
        if row in selected:
            continue
        c=(row['box'][1]+row['box'][3])/2
        distance=first-c if c<first else c-last if c>last else None
        width=min(box[2],row['box'][2])-max(box[0],row['box'][0])
        if (distance is not None and distance<=1.35*pitch and width>0 and
                abs(row['box'][0]-box[0])<=2*line_height):
            return True
    return False


def _exact_span_is_closed(snap, boundary, blocks):
    """Do not accept a normalized prefix while dropping other native glyphs."""
    lines=_page_lines(blocks)
    if lines is None:
        return False
    selected_ids=set(snap['line_ids'])
    rows=_visual_rows(lines)
    selected=[]
    for row in rows:
        membership=[p['line']['id'] in selected_ids for p in row['fragments']]
        if any(membership):
            if not all(membership):
                return False
            selected.append(row)
        else:
            for part in row['fragments']:
                for glyph in part['line']['glyphs']:
                    x0,y0,x1,y1=glyph['bbox_pt']; x=(x0+x1)/2; y=(y0+y1)/2
                    if boundary[0]<=x<=boundary[2] and boundary[1]<=y<=boundary[3]:
                        return False
    return bool(selected) and not _adjacent_line_may_continue(rows,selected)


def _boundary_identity(text, boundary, blocks, identity):
    """Close complete native lines under a tight independent caption boundary."""
    def fail(reason, status='unavailable'):
        return {'status':status,'reason':reason}
    if prefix_identity(text) and not _has_label_separator(text,identity):
        return fail('PF2 caption lacks a complete labelled prefix')
    lines=_page_lines(blocks)
    if lines is None:
        return fail('page has missing, invalid, or unlocated native glyphs')
    if not lines:
        return fail('page has no native glyphs')
    rows=_visual_rows(lines)

    # Count native prefix starts throughout the page, including a label split
    # across adjacent native fragments.  A body reference in the middle of a
    # row is not treated as a prefix.
    starts=set()
    for row in rows:
        for part in row['fragments']:
            parsed=prefix_identity(part['line']['text'])
            if parsed and parsed[:2]==identity:
                starts.add(part['order'])
        parsed=prefix_identity(row['text'])
        if parsed and parsed[:2]==identity:
            starts.add(row['fragments'][0]['order'])
    if len(starts)!=1:
        return fail('native page does not have a unique complete caption prefix',
                    'ambiguous' if len(starts)>1 else 'unavailable')
    prefix_order=next(iter(starts))
    prefix_rows=[r for r in rows if r['fragments'][0]['order']==prefix_order and
                 _has_label_separator(r['text'],identity)]
    if len(prefix_rows)!=1:
        return fail('native prefix is not a complete labelled row')
    prefix_row=prefix_rows[0]

    def intersects(a,b):
        return min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1])
    def center_in(box,b):
        x=(box[0]+box[2])/2; y=(box[1]+box[3])/2
        return b[0]<=x<=b[2] and b[1]<=y<=b[3]
    selected=[]
    for row in rows:
        glyphs=[g for part in row['fragments'] for g in part['line']['glyphs']]
        if any(center_in(g['bbox_pt'],boundary) for g in glyphs):
            selected.append(row)
        elif any(intersects(g['bbox_pt'],boundary) for g in glyphs):
            return fail('caption boundary crosses an unselected native line', 'ambiguous')
    if prefix_row not in selected:
        return fail('native prefix lies outside the PF2 caption boundary')
    if sum(_label_count(row['text']) for row in selected)>1:
        return fail('caption boundary contains another prefix', 'ambiguous')
    # A transitive merge of different baselines or disjoint columns cannot be
    # treated as one caption row, even if the enclosing rectangle overlaps.
    for i,row in enumerate(selected):
        fragments=row['fragments']
        if any(not _same_band(a['box'],b['box']) for k,a in enumerate(fragments)
               for b in fragments[k+1:]):
            return fail('native row has an ambiguous baseline', 'ambiguous')
        if any(_same_band(row['box'],other['box']) for other in selected[i+1:]):
            return fail('caption boundary spans disconnected columns or row fragments', 'ambiguous')
        if row is not prefix_row and row['box'][3]<=prefix_row['box'][1]:
            return fail('native content precedes the caption prefix', 'ambiguous')
        for part in fragments:
            parsed=prefix_identity(part['line']['text'])
            if parsed and part['order']!=prefix_order:
                return fail('caption boundary contains another prefix', 'ambiguous')

    chosen=sorted((part for row in selected for part in row['fragments']),key=lambda p:p['order'])
    if not chosen or chosen[0]['order']!=prefix_order:
        return fail('native reading order does not start with the caption prefix')
    box=union([part['box'] for part in chosen])
    heights=[row['box'][3]-row['box'][1] for row in selected]
    line_height=statistics.median(heights)
    tolerance=max(3.0,0.35*line_height)
    if any(abs(a-b)>tolerance for a,b in zip(box,boundary)):
        return fail('PF2 and native caption extents do not close')

    # A nearby line on the same text measure may be a clipped continuation.
    # Do not infer its ownership from proximity; leave that case for review.
    if _adjacent_line_may_continue(rows,selected):
        return fail('adjacent native line may continue the caption', 'ambiguous')

    source='\n'.join(part['line']['text'] for part in chosen)
    # The native order and exact line identities remain untouched in the
    # output.  Use visual rows only for the bounded extractor check, including
    # a prefix split by fonts, with glyph identity retained for '?' matching.
    layout_rows=sorted(selected,key=lambda row:(row['box'][1],row['box'][0]))
    layout_source='\n'.join(row['text'] for row in layout_rows)
    layout_glyphs=[g for row in layout_rows for part in row['fragments']
                   for g in part['line']['glyphs']]
    if not _compatible_pf2_text(layout_glyphs,text):
        return fail('PF2 text is inconsistent with the closed native lines')
    same_text=' '.join(layout_source.split())==' '.join(text.split())
    glyphs=[g for part in chosen for g in part['line']['glyphs']]
    candidate=matching_text(source)
    return {'status':'ok','alignment_method':'native_boundary_identity','prefix_label_separator':True,
            'text_equivalence':'normalized_exact' if same_text else 'not_established',
            'pf2_text_comparison':'literal_whitespace_equal' if same_text
                                  else 'bounded_glyph_compatible',
            'source_snap':{'line_ids':[part['line']['id'] for part in chosen],
                           'lines':[part['line']['text'] for part in chosen],
                           'text':source,'normalized':candidate,'source_snap_bbox_pt':box,
                           'glyphs':glyphs,'pf2_edge_distance_pt':edges_distance(box,boundary)}}


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
    try:
        b=[float(boundary[k]) for k in ('x1','y1','x2','y2')]
    except (KeyError,TypeError,ValueError):
        return {'status':'unavailable','reason':'invalid PF2 text or boundary',
                'origin':origin,'predictor':item}
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
    if len(matches)>1:
        result.update(status='ambiguous',reason='multiple matching line spans')
    elif len(matches)==1 and _label_count(matches[0]['text'])>1:
        result.update(status='ambiguous',reason='caption span contains another prefix')
    elif len(matches)==1 and _exact_span_is_closed(matches[0],b,blocks):
        result.update(status='ok',alignment_method='exact_text',
                      text_equivalence='normalized_exact',source_snap=matches[0],
                      prefix_label_separator=_has_label_separator(
                          matches[0]['text'],(expected_type,expected_number)))
    else:
        result.update(_boundary_identity(text,b,blocks,(expected_type,expected_number)))
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
    if x1<=x0 or y1<=y0:
        return None
    # The panel and caption barrier establish the safe extent.  Segmentation's
    # dark-core threshold is not a visible-pixel boundary: preserve every
    # non-white source RGB pixel, including light antialiasing and colour.
    a=np.asarray(image.crop((x0,y0,x1,y1)).convert('RGB'))
    ys,xs=np.nonzero(np.any(a!=255,axis=2))
    return [int(x0+xs.min()),int(y0+ys.min()),int(x0+xs.max()+1),int(y0+ys.max()+1)] if len(xs) else None
