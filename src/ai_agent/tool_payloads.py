"""Extractive tool-result packing. Keeps JSON valid and preserves provenance."""
from __future__ import annotations
import json
import re


def _bounded(value, *, strings=500, items=12, depth=0):
    if isinstance(value, str):
        return value if len(value) <= strings else value[:strings] + ' [excerpt]'
    if isinstance(value, dict):
        if depth >= 5:
            return {'_excerpt': 'nested object omitted'}
        priority = {'@type','name','title','headline','url','datePublished','dateModified','author','publisher',
                    'identifier','isbn','price','priceCurrency','currency','authors','offers','access',
                    'provider','provider_id','offer_type','region','text','status'}
        selected = [(key,item) for key,item in value.items() if key in priority]
        selected.extend((key,item) for key,item in value.items() if key not in priority)
        count = max(items,sum(key in priority for key in value))
        return {str(key): _bounded(item, strings=strings, items=items, depth=depth+1)
                for key, item in selected[:count]
                if key not in {'articleBody', 'embedded_data'}}
    if isinstance(value, list):
        return [_bounded(item, strings=strings, items=items, depth=depth+1) for item in value[:items]] if depth < 5 else ['[nested list omitted]']
    return value


def text_excerpt(text: str, limit: int, query: str = '') -> tuple[str, list[list[int]]]:
    """Keep exact source slices; no generated summary or fabricated continuity."""
    if len(text) <= limit:
        return text, [[0, len(text)]]
    terms = {term.casefold() for term in re.findall(r'[\w-]{4,}', query)}
    terms -= {'research', 'read', 'http', 'https', 'source', 'данных', 'ссылки', 'укажи', 'нужно', 'можешь', 'пожалуйста'}
    for pattern, equivalents in [
        (r'инструмент|вызов', {'tool','calling','function','agentic'}),
        (r'метод|бенчмарк|исследован', {'method','benchmark','evaluation','results'}),
        (r'огранич', {'limitation','limitations'}),
        (r'русск|язык', {'russian','multilingual','languages'}),
        (r'локальн|запуск', {'local','deployment','ollama','llama'}),
    ]:
        if re.search(pattern,query,re.I):
            terms.update(equivalents)
    width = max(1,min(limit,max(180,min(1200,limit//3))))
    chunks = [(start, min(len(text),start+width)) for start in range(0,len(text),width)]
    chunk_terms = {span:set(re.findall(r'[\w-]{4,}',text[span[0]:span[1]].casefold())) for span in chunks}
    remaining_chunks = list(chunks[1:])
    covered = terms & chunk_terms[chunks[0]]
    selected = [chunks[0]]
    used = chunks[0][1]
    marker = '\n[...source excerpt...]\n'
    while remaining_chunks:
        # Prefer a new requested facet over a second passage about a facet that
        # is already represented (e.g. tools as well as local deployment).
        span = max(remaining_chunks,key=lambda part:(len((terms-covered)&chunk_terms[part]),len(terms&chunk_terms[part]),-part[0]))
        remaining_chunks.remove(span)
        remaining = limit-used-len(marker)
        if remaining < 100:
            break
        clipped = (span[0],min(span[1],span[0]+remaining))
        selected.append(clipped)
        covered.update(terms & set(re.findall(r'[\w-]{4,}',text[clipped[0]:clipped[1]].casefold())))
        used += clipped[1]-clipped[0]+len(marker)
    selected.sort()
    return marker.join(text[start:end] for start,end in selected), [list(span) for span in selected]


def compact_tool_output(content: str, max_chars: int, *, query: str = '') -> str:
    """Maintain the untrusted-data wrapper and JSON rather than slicing its prefix."""
    if len(content) <= max_chars:
        return content
    prefix = suffix = ''
    body = content
    if content.startswith('<untrusted_tool_output '):
        header_end = content.find('\n',content.find('\n')+1)
        footer = content.rfind('\n</untrusted_tool_output>')
        if header_end >= 0 and footer > header_end:
            prefix,body,suffix = content[:header_end+1],content[header_end+1:footer],content[footer:]
    available = max(400,max_chars-len(prefix)-len(suffix))
    try:
        payload = json.loads(body)
    except (ValueError,TypeError):
        excerpt,_ = text_excerpt(body,max(100,available-70),query)
        return prefix + excerpt + '\n[tool result excerpt; omitted content is not verified here]' + suffix
    if isinstance(payload,dict) and isinstance(payload.get('content'),str) and all(
            key in payload for key in ('start_line','end_line','total_lines','next_start_line','sha256')):
        return prefix + _compact_file_lines(payload,available) + suffix
    if isinstance(payload,dict) and (isinstance(payload.get('fields'),dict) or 'main_text' in payload):
        fields = payload.get('fields') if isinstance(payload.get('fields'),dict) else payload
        main = str(fields.get('main_text',''))
        # Title, dates, item-specific offers and URL survive even a small budget.
        packed = {key:payload[key] for key in ['url','retrieved_at','locale','region','status','adapter','extraction_method','confidence','safety'] if key in payload}
        fixed = {key:_bounded(fields[key],strings=400,items=8) for key in ['title','metadata','catalog_item','merchant_offer','json_ld'] if key in fields}
        packed['fields'] = fixed
        for size in [max(200,available*2//3), max(180,available//2), max(150,available//4), 0]:
            excerpt,spans = text_excerpt(main,size,query) if size else ('',[])
            packed['fields']['main_text'] = excerpt
            packed['excerpt'] = {'original_chars':len(body),'main_text_chars':len(main),'main_text_spans':spans,'omitted':True,'method':'exact source slices; use research_read again for omitted facts'}
            encoded = json.dumps(packed,ensure_ascii=False,separators=(',',':'))
            if len(encoded) <= available:
                return prefix+encoded+suffix
            # Structured descriptions can also duplicate the entire article.
            packed['fields']['json_ld'] = _bounded(fixed.get('json_ld',[]),strings=120,items=4)
        packed = {'url':str(payload.get('url',''))[:600], 'status':payload.get('status'),
                  'fields':{'title':str(fields.get('title',''))[:250],
                            'catalog_item':_bounded(fields.get('catalog_item',{}),strings=180,items=12),
                            'merchant_offer':_bounded(fields.get('merchant_offer',{}),strings=180,items=8)},
                  'excerpt':{'omitted':True,'note':'Body omitted due to context budget; read source again before using missing facts.'}}
    else:
        packed = {'tool_result_excerpt':_bounded(payload,strings=250,items=8),'omitted':True}
    encoded = json.dumps(packed,ensure_ascii=False,separators=(',',':'))
    if len(encoded)>available:
        # Width/depth caps alone do not bound a broad JSON object. The budget
        # is real, including browser snapshots with many elements/trackers.
        fields=payload.get('fields',payload) if isinstance(payload,dict) else {}
        packed={'omitted':True,'note':'Observation omitted to fit context. Read the source for missing details.'}
        if isinstance(fields,dict):
            for key in ('url','title','text','main_text'):
                value=(payload.get(key) or fields.get(key)) if isinstance(payload,dict) else None
                if not isinstance(value,str):continue
                if key=='url':
                    if len(value)<available//2:packed[key]=value
                else:packed[key]=text_excerpt(value,max(50,available//5),query)[0]
                if len(json.dumps(packed,ensure_ascii=False,separators=(',',':')))>available:
                    packed.pop(key)
        encoded=json.dumps(packed,ensure_ascii=False,separators=(',',':'))
    return prefix+encoded+suffix


def _compact_file_lines(payload: dict, limit: int) -> str:
    """Keep contiguous code and accurate continuation, never a 250-char gist.

    Generic JSON packing loses almost all code yet keeps the original line
    range. File reads must instead describe precisely what the model sees.
    """
    content = payload['content']
    def pack(count):
        excerpt = content[:count]
        boundaries = list(re.finditer(r'(?m)^(\d+): ',excerpt))
        result = {**payload,'content':excerpt,'truncated':True,
            'excerpt':{'omitted':True,'method':'contiguous prefix; continue with next_start_line/next_start_column'}}
        if boundaries:
            last = boundaries[-1]
            number = int(last.group(1))
            column = int(payload.get('start_column',1)) if number==payload['start_line'] else 1
            result.update(end_line=number,next_start_line=number,
                next_start_column=column+len(excerpt[last.end():]))
        else:
            result.update(content='',end_line=payload['start_line']-1,
                next_start_line=payload['start_line'],next_start_column=payload.get('start_column',1))
        if count==len(content):
            for key in ('end_line','truncated','next_start_line','next_start_column'):result[key]=payload[key]
        return json.dumps(result,ensure_ascii=False,separators=(',',':'))
    lo,hi=0,len(content)
    while lo<hi:
        mid=(lo+hi+1)//2
        if len(pack(mid))<=limit:lo=mid
        else:hi=mid-1
    # Prefer ending at a complete line. A single long line uses a column
    # continuation so even minified code can be read without omissions.
    boundary=content.rfind('\n',0,lo)
    if boundary>0 and lo<len(content):
        result=json.loads(pack(boundary))
        result.update(next_start_line=result['end_line']+1,next_start_column=1)
        encoded=json.dumps(result,ensure_ascii=False,separators=(',',':'))
    else:encoded=pack(lo)
    if len(encoded)>limit:
        return json.dumps({'omitted':True,'note':'File metadata exceeds context budget; request a shorter file path or smaller read.'},separators=(',',':'))
    return encoded
