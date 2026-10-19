"""Translate known business status fields in public Markdown, without changing data IDs."""
import re

ORDER = {'1':'待审核', '2':'已审核', '3':'采购中', '4':'已入库', '5':'已取消'}
SUPPLIER = {'1':'合作中', '2':'暂停合作', '3':'已终止'}
LABEL = r'(订单状态|供应商(?:合作)?状态|合作状态)'

def status_name(value, kind):
    match = re.fullmatch(r'\s*[*`]*([0-9]+)[*`]*\s*(?:[（(][^）)]*[）)][*`]*)?\s*', value)
    if not match:
        return value
    return (SUPPLIER if kind == 'supplier' else ORDER).get(match[1], '未知状态')

def business_status_text(text):
    columns = {}
    fenced = False
    result = []
    for line in text.split('\n'):
        if line.lstrip().startswith('```'):
            fenced = not fenced
        if fenced:
            result.append(line)
            continue
        line = re.sub(LABEL + r'([*`]*\s*[:：=]\s*)[*`]*([0-9]+)[*`]*(?:\s*[（(][^）)\n]*[）)][*`]*)?',
            lambda m: m[1] + m[2] + (SUPPLIER if '供应商' in m[1] or m[1] == '合作状态' else ORDER).get(m[3], '未知状态'), line)
        if line.strip().startswith('|'):
            cells = line.split('|')
            headers = [cell.strip().replace('*','').replace('`','') for cell in cells]
            if len(cells) >= 3 and len(headers)>2 and headers[1] in {'订单状态','供应商合作状态','供应商状态','合作状态'} and re.match(r'\s*[*`]*[0-9]', cells[2]):
                cells[2] = ' ' + status_name(cells[2], 'supplier' if '供应商' in headers[1] or headers[1]=='合作状态' else 'order').strip() + ' '
            else:
                identified = {}
                for index, header in enumerate(headers):
                    if '状态' not in header:
                        continue
                    if '供应商' in header or '合作' in header:
                        identified[index] = 'supplier'
                    elif '订单' in header:
                        identified[index] = 'order'
                    elif header == '状态':
                        if any(re.search(r'订单|下单|order', h, re.I) for h in headers):
                            identified[index] = 'order'
                        elif any('供应商' in h for h in headers):
                            identified[index] = 'supplier'
                if identified:
                    columns = identified
                else:
                    for index, kind in columns.items():
                        if index < len(cells):
                            cells[index] = ' ' + status_name(cells[index], kind).strip() + ' '
            line = '|'.join(cells)
        else:
            columns = {}
        result.append(line)
    return '\n'.join(result)
