"""Copy-on-write Office edits that preserve opaque macro binaries; never run VBA."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import posixpath
import re
from lxml import etree as ET
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

from ai_agent.tools.base import Tool, ToolPermission
from ai_agent.tools.files import FileSandbox

SUFFIXES = {'.docx', '.docm', '.dotm', '.xlsx', '.xlsm', '.xltm', '.pptx', '.pptm', '.potm'}
SPREADSHEETS = {'.xlsx', '.xlsm', '.xltm'}
S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'


def _xml(data):
    # lxml preserves namespace declarations used by mc:Ignorable/Office extensions.
    tree = ET.fromstring(data, parser=ET.XMLParser(resolve_entities=False, no_network=True))
    if tree.getroottree().docinfo.doctype:
        raise ValueError('DTD в Office XML не поддерживается.')
    return tree


class OfficeSandbox:
    def __init__(self, files: FileSandbox):
        self.files = files

    def _package(self, path):
        source = self.files.resolve_download_path(path)
        if source.suffix.lower() not in SUFFIXES:
            raise ValueError('Поддерживаются современные DOCX/DOCM, XLSX/XLSM, PPTX/PPTM и шаблоны с макросами. VBA не выполняется.')
        if source.stat().st_size > 40 * 1024 * 1024:
            raise ValueError('Документ превышает 40 МБ.')
        data = source.read_bytes()
        try:
            with ZipFile(BytesIO(data)) as package:
                members = package.infolist()
                if len(members) > 5000 or sum(i.file_size for i in members) > 40 * 1024 * 1024:
                    raise ValueError('Распакованный документ слишком большой.')
                if len({i.filename for i in members}) != len(members):
                    raise ValueError('Документ содержит неоднозначные повторные части.')
                entries = {i.filename: package.read(i) for i in members}
        except BadZipFile as exc:
            raise ValueError('Повреждённый или зашифрованный Office-документ.') from exc
        return source, data, members, entries

    @staticmethod
    def _parts(entries):
        return [name for name in entries if name == 'word/document.xml'
                or re.fullmatch(r'word/(header|footer)\d+\.xml', name)
                or re.fullmatch(r'ppt/slides/slide\d+\.xml', name)
                or name == 'xl/sharedStrings.xml']

    @staticmethod
    def _sheets(entries):
        if 'xl/workbook.xml' not in entries:
            return {}
        tree = _xml(entries['xl/workbook.xml'])
        relationships = _xml(entries['xl/_rels/workbook.xml.rels'])
        targets = {r.get('Id'): r.get('Target') for r in relationships if r.get('TargetMode') != 'External'}
        result = {}
        for sheet in tree.findall(f'.//{{{S}}}sheet'):
            target = targets.get(sheet.get(f'{{{R}}}id'), '')
            part = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            if part.startswith('xl/worksheets/') and part in entries:
                result[sheet.get('name')] = part
        return result

    def inspect(self, path):
        source, content, _, entries = self._package(path)
        parts = []
        remaining = 24000
        for name in self._parts(entries):
            tree = _xml(entries[name])
            text = '\n'.join(node.text for node in tree.iter() if isinstance(node.tag, str) and node.tag.endswith('}t') and node.text)
            parts.append({'part': name, 'text': text[:remaining]})
            remaining -= min(len(text), remaining)
            if remaining <= 0:
                break
        sheets = []
        strings = []
        if 'xl/sharedStrings.xml' in entries:
            strings = [''.join(n.itertext()) for n in _xml(entries['xl/sharedStrings.xml'])]
        for name, part in self._sheets(entries).items():
            cells = []
            for c in _xml(entries[part]).iter(f'{{{S}}}c'):
                v, f = c.find(f'{{{S}}}v'), c.find(f'{{{S}}}f')
                value = v.text if v is not None else ''.join(c.itertext())
                if c.get('t') == 's' and value and value.isdigit():
                    value = strings[int(value)] if int(value) < len(strings) else '[invalid shared string]'
                cells.append({'cell': c.get('r'), 'value': str(value or '')[:500], 'formula': f.text if f is not None else None})
                if len(cells) >= 200:
                    break
            sheets.append({'sheet': name, 'cells': cells, 'limit': 200})
            if len(sheets) >= 20:
                break
        return {'path': path, 'sha256': sha256(content).hexdigest(), 'text_parts': parts, 'sheets': sheets,
                'macros_present': any(n.lower().endswith('vbaproject.bin') for n in entries),
                'note': 'Показан ограниченный фрагмент; макросы не выполнялись, формулы не пересчитывались.'}

    def edit(self, path, output_path, expected_sha256, replacements=None, cells=None):
        source, content, members, entries = self._package(path)
        if sha256(content).hexdigest() != expected_sha256.lower():
            raise ValueError('stale_file: документ изменился после чтения.')
        target = self.files._resolve_safe_path(output_path)
        if (not target.is_relative_to(self.files.workspace_dir / 'outputs') or target == source
                or target.suffix.lower() != source.suffix.lower() or target.exists()):
            raise ValueError('Нужен новый файл в outputs/ с тем же расширением; оригинал не изменяется.')
        if any(n.startswith('_xmlsignatures/') for n in entries):
            raise ValueError('Пакет подписан цифровой подписью. Редактирование сделает её недействительной; сначала нужна неподписанная копия.')
        replacements, cells = replacements or [], cells or []
        if not replacements and not cells:
            raise ValueError('Нужны replacements или cells.')
        if len(replacements) > 100 or len(cells) > 500:
            raise ValueError('Слишком много изменений за один вызов.')
        if replacements and source.suffix.lower() in SPREADSHEETS:
            raise ValueError('Для Excel используй cells[] с точным листом и адресом ячейки. replacements может изменить несколько ячеек с общей строкой и не поддерживается.')
        changed = {}
        for replacement in replacements:
            old, new = str(replacement['old']), str(replacement['new'])
            if not old or len(old) + len(new) > 24000:
                raise ValueError('Пустая или слишком большая замена.')
            matches = []
            for name in self._parts(entries):
                tree = changed.get(name)
                if tree is None:
                    tree = _xml(entries[name])
                for node in tree.iter():
                    if isinstance(node.tag, str) and node.tag.endswith('}t') and node.text and old in node.text:
                        matches.extend([(name, tree, node)] * node.text.count(old))
            if len(matches) != 1:
                raise ValueError(f'exact_match_required: текст должен встречаться в одном текстовом фрагменте ровно один раз (найдено {len(matches)}).')
            name, tree, node = matches[0]
            node.text = node.text.replace(old, new, 1)
            changed[name] = tree
        sheets = self._sheets(entries) if cells else {}
        for edit in cells:
            part = sheets.get(edit['sheet'])
            address = str(edit['cell']).upper()
            match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})', address)
            if not part or not match or int(match[2]) > 1048576 or _column(match[1]) > 16384:
                raise ValueError('Неверное имя листа или адрес ячейки.')
            tree = changed.get(part)
            if tree is None:
                tree = _xml(entries[part])
            for merged in tree.iter(f'{{{S}}}mergeCell'):
                reference = merged.get('ref', '').replace('$', '').upper()
                if _in_range(address, reference) and address != reference.split(':', 1)[0]:
                    raise ValueError('Ячейка входит в объединённый диапазон. Измени его верхнюю левую ячейку.')
            # Editing a member of an array/shared formula can corrupt the whole
            # group. This value editor refuses those ranges instead of breaking it.
            for formula in tree.iter(f'{{{S}}}f'):
                if formula.get('t') in {'array', 'shared', 'dataTable'}:
                    parent = formula.getparent()
                    if parent.get('r') == address or _in_range(address, formula.get('ref', '')):
                        raise ValueError('Ячейка входит в составную формулу. Изменение этого диапазона пока не поддерживается.')
            data = tree.find(f'{{{S}}}sheetData')
            if data is None:
                raise ValueError('Лист не содержит sheetData.')
            rownum = int(match[2])
            row = next((r for r in data if int(r.get('r', 0)) == rownum), None)
            if row is None:
                row = ET.Element(f'{{{S}}}row', {'r': str(rownum)})
                index = next((i for i, r in enumerate(data) if int(r.get('r', 0)) > rownum), len(data))
                data.insert(index, row)
            cell = next((c for c in row if c.get('r') == address), None)
            if cell is None:
                cell = ET.Element(f'{{{S}}}c', {'r': address})
                col = _column(match[1])
                index = next((i for i, c in enumerate(row) if _column(re.sub(r'\d', '', c.get('r', 'A1'))) > col), len(row))
                row.insert(index, cell)
            if cell.find(f'{{{S}}}f') is not None:
                raise ValueError('Изменение формул этим инструментом не поддерживается; выбери ячейку с исходными данными.')
            for node in list(cell):
                cell.remove(node)
            cell.attrib.pop('t', None)
            value = edit.get('value')
            if value is None:
                pass
            elif isinstance(value, bool):
                cell.set('t', 'b')
                ET.SubElement(cell, f'{{{S}}}v').text = '1' if value else '0'
            elif isinstance(value, (int, float)):
                import math
                if not math.isfinite(value):
                    raise ValueError('Число должно быть конечным.')
                ET.SubElement(cell, f'{{{S}}}v').text = str(value)
            else:
                text = str(value)
                if len(text) > 32767:
                    raise ValueError('Текст ячейки превышает 32767 символов.')
                cell.set('t', 'inlineStr')
                node = ET.SubElement(ET.SubElement(cell, f'{{{S}}}is'), f'{{{S}}}t')
                node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                node.text = text  # Leading '=' is literal text, not an injected formula.
            changed[part] = tree
        if cells or 'xl/sharedStrings.xml' in changed:
            # Invalidate cached formula results; Excel must recalculate on open.
            for name in entries:
                if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', name):
                    tree = changed.get(name)
                    if tree is None:
                        tree = _xml(entries[name])
                    for cell in tree.iter(f'{{{S}}}c'):
                        if cell.find(f'{{{S}}}f') is not None:
                            v = cell.find(f'{{{S}}}v')
                            if v is not None:
                                cell.remove(v)
                    changed[name] = tree
            workbook = _xml(entries['xl/workbook.xml'])
            calc = workbook.find(f'{{{S}}}calcPr')
            if calc is None:
                calc = ET.Element(f'{{{S}}}calcPr')
                following = {f'{{{S}}}{name}' for name in (
                    'oleSize', 'customWorkbookViews', 'pivotCaches', 'smartTagPr',
                    'smartTagTypes', 'webPublishing', 'fileRecoveryPr',
                    'webPublishObjects', 'extLst')}
                index = next((i for i, node in enumerate(workbook) if node.tag in following), len(workbook))
                workbook.insert(index, calc)
            calc.set('fullCalcOnLoad', '1')
            calc.set('forceFullCalc', '1')
            calc.set('calcMode', 'auto')
            changed['xl/workbook.xml'] = workbook
        for name, tree in changed.items():
            entries[name] = ET.tostring(tree, encoding='utf-8', xml_declaration=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        result = BytesIO()
        with ZipFile(result, 'w', compression=ZIP_DEFLATED) as package:
            for info in members:
                package.writestr(info, entries[info.filename])
        if sha256(source.read_bytes()).hexdigest() != expected_sha256.lower():
            raise ValueError('stale_file: исходник изменился во время обработки.')
        with target.open('xb') as handle:
            handle.write(result.getvalue())
        return {'path': target.relative_to(self.files.workspace_dir).as_posix(), 'size_bytes': target.stat().st_size,
                'changed_parts': sorted(changed), 'macros_preserved': True, 'source_preserved': True,
                'note': 'Макросы сохранены побайтно и не выполнялись. Excel пересчитает формулы при открытии.'}


def _column(letters):
    result = 0
    for char in letters:
        result = result * 26 + ord(char) - 64
    return result


def _in_range(address, reference):
    values = reference.replace('$', '').split(':')
    if len(values) != 2:
        return address == reference
    def point(value):
        match = re.fullmatch(r'([A-Z]+)([1-9][0-9]*)', value)
        return (_column(match[1]), int(match[2])) if match else None
    start, end, cell = point(values[0]), point(values[1]), point(address)
    return bool(start and end and cell and start[0] <= cell[0] <= end[0] and start[1] <= cell[1] <= end[1])


def register_office_tools(registry, files):
    office = OfficeSandbox(files)
    registry.register(Tool(name='inspect_office_document', description='Читает текст/ячейки современного Office, включая DOCM/XLSM/PPTM, возвращает SHA-256. Макросы не запускает.',
        schema={'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']},
        output_schema={'type': 'object'}, permissions=(ToolPermission('workspace.read', 'read'),), capabilities=('office.read',), handler=lambda a: office.inspect(a['path'])))
    registry.register(Tool(name='edit_office_document', description='Создаёт изменённую копию Office в outputs/ с сохранением VBA. Сначала inspect_office_document для SHA-256. replacements заменяет один точный текстовый фрагмент Word/PowerPoint; для Excel используй только cells с точным листом/ячейкой. VBA/формулы не исполняет; расширение сохраняется.',
        schema={'type': 'object', 'properties': {'path': {'type': 'string'}, 'output_path': {'type': 'string'}, 'expected_sha256': {'type': 'string'},
            'replacements': {'type': 'array', 'items': {'type': 'object', 'properties': {'old': {'type': 'string'}, 'new': {'type': 'string'}}, 'required': ['old', 'new']}},
            'cells': {'type': 'array', 'items': {'type': 'object', 'properties': {'sheet': {'type': 'string'}, 'cell': {'type': 'string'}, 'value': {'type': ['string', 'number', 'boolean', 'null']}}, 'required': ['sheet', 'cell', 'value']}}},
            'required': ['path', 'output_path', 'expected_sha256']},
        output_schema={'type': 'object'}, permissions=(ToolPermission('workspace.write', 'write'),), capabilities=('office.edit',), handler=lambda a: office.edit(**a)))
