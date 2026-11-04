"""Synthetic end-to-end image/Office fixtures; user uploads are never modified."""
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PIL import Image
from fastapi.testclient import TestClient
from backend.main import app
from backend.service import AgentService
from backend.attachments import validate_attachment, extract_text
from ai_agent.config import Settings
from ai_agent.database import AvelinDatabase
from ai_agent.llm.vision import VisionRouter
from ai_agent.tools.base import ToolRegistry
from ai_agent.tools.files import FileSandbox
from ai_agent.tools.media import ImageSandbox, register_image_tools
from ai_agent.tools.office_files import OfficeSandbox, register_office_tools, _xml, S


def image_bytes(format='PNG', size=(800, 400)):
    image = Image.new('RGB', size, '#8f48bd')
    buffer = BytesIO()
    image.save(buffer, format)
    return buffer.getvalue()


def office_bytes(kind='xlsm'):
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as package:
        package.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        if kind == 'xlsm':
            package.writestr('xl/workbook.xml', f'<workbook xmlns="{S}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Лист 1" sheetId="1" r:id="rId1"/></sheets></workbook>')
            package.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            package.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{S}" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x14ac="http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac" mc:Ignorable="x14ac"><sheetData><row r="1"><c r="A1" s="3" t="inlineStr"><is><t>Расходы</t></is></c><c r="B1"><f>SUM(B2:B3)</f><v>10</v></c></row></sheetData></worksheet>')
            package.writestr('xl/vbaProject.bin', b'opaque-vba-fixture\x00\xff')
        else:
            package.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Отчёт октября</w:t></w:r></w:p></w:body></w:document>')
            package.writestr('word/vbaProject.bin', b'word-vba-fixture\x00\xff')
    return output.getvalue()


def office_with_parts(parts):
    """Keep the macro-bearing fixture intact while adding real OOXML cases."""
    output = BytesIO()
    with ZipFile(BytesIO(office_bytes())) as original, ZipFile(output, 'w', compression=ZIP_DEFLATED) as package:
        for name in original.namelist():
            package.writestr(name, parts.get(name, original.read(name)))
        for name, data in parts.items():
            if name not in original.namelist():
                package.writestr(name, data)
    return output.getvalue()


class MediaOfficeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.files = FileSandbox(self.root / 'workspace')
        self.images = ImageSandbox(self.files)
        self.office = OfficeSandbox(self.files)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, name, data):
        validate_attachment(name, data)
        path = self.files.workspace_dir / name
        path.write_bytes(data)
        return path

    def test_real_heic_upload_vision_normalization_preview_and_jpeg_output(self):
        content = image_bytes('HEIF')
        source = self.save('iphone.heic', content)
        self.assertIn('analyze_image', extract_text(source))
        captured = []
        class Vision:
            name, model = 'fixture', 'test'
            def analyze(self, data, mime, prompt):
                captured.append((data, mime))
                return 'Purple fixture'
        self.assertEqual(VisionRouter(self.files.workspace_dir, [Vision()]).analyze_path('iphone.heic', 'colour').text, 'Purple fixture')
        self.assertEqual(captured[0][1], 'image/jpeg')
        self.assertEqual(Image.open(BytesIO(captured[0][0])).size, (800, 400))
        result = self.images.transform('iphone.heic', 'outputs/resized.jpg', width=200, format='jpg')
        self.assertEqual((result['width'], result['height']), (200, 100))
        with Image.open(self.files.workspace_dir / result['path']) as saved:
            self.assertEqual(saved.format, 'JPEG')
        self.assertEqual(source.read_bytes(), content)

    def test_popular_formats_decode_actual_pixels_and_mismatches_are_rejected(self):
        for suffix, format in [('.png', 'PNG'), ('.jpg', 'JPEG'), ('.webp', 'WEBP'), ('.avif', 'AVIF'), ('.bmp', 'BMP'), ('.gif', 'GIF'), ('.tiff', 'TIFF'), ('.heif', 'HEIF')]:
            with self.subTest(suffix=suffix):
                validate_attachment('photo' + suffix, image_bytes(format, (20, 10)))
        with self.assertRaises(ValueError):
            validate_attachment('fake.heic', image_bytes())

    def test_resize_fit_modes_transparency_metadata_and_source_are_preserved(self):
        image = Image.new('RGBA', (300, 100), (255, 0, 0, 0))
        image.info['comment'] = b'private fixture'
        output = BytesIO()
        image.save(output, 'PNG')
        source = self.save('transparent.png', output.getvalue())
        before = source.read_bytes()
        for fit, expected in [('contain', (100, 33)), ('cover', (100, 100)), ('stretch', (100, 100))]:
            result = self.images.transform(source.name, f'outputs/{fit}.jpg', width=100, height=100, fit=fit)
            with Image.open(self.files.workspace_dir / result['path']) as actual:
                self.assertEqual(actual.size, expected)
                self.assertEqual(actual.mode, 'RGB')
                self.assertNotIn('exif', actual.info)
        self.assertEqual(source.read_bytes(), before)

    def test_transform_rejects_overwrite_escape_and_pixel_bomb(self):
        self.save('source.png', image_bytes())
        for args in [{'output_path': '../escape.png'}, {'output_path': 'source.png'}, {'output_path': 'outputs/x.png', 'width': 16000, 'height': 16000}]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.images.transform('source.png', **args)
        self.images.transform('source.png', 'outputs/once.png', width=10)
        with self.assertRaises(ValueError):
            self.images.transform('source.png', 'outputs/once.png', width=10)

    def test_registry_invocation_returns_objects_and_files_instead_of_schema_failure(self):
        self.save('image.png', image_bytes())
        self.save('book.xlsm', office_bytes())
        registry = ToolRegistry()
        register_image_tools(registry, self.files)
        register_office_tools(registry, self.files)
        inspected = registry.invoke('inspect_image', {'path': 'image.png'})
        self.assertEqual(inspected.status, 'completed', inspected.text())
        result = registry.invoke('transform_image', {'path': 'image.png', 'width': 80, 'format': 'webp'})
        self.assertEqual(result.status, 'completed', result.text())
        book = registry.invoke('inspect_office_document', {'path': 'book.xlsm'})
        self.assertEqual(book.status, 'completed', book.text())
        edited = registry.invoke('edit_office_document', {'path': 'book.xlsm', 'output_path': 'outputs/book.xlsm',
            'expected_sha256': book.output['sha256'], 'cells': [{'sheet': 'Лист 1', 'cell': 'C3', 'value': 42}]})
        self.assertEqual(edited.status, 'completed', edited.text())

    def test_xlsm_edit_preserves_macros_namespaces_styles_and_invalidates_formula_cache(self):
        source = self.save('book.xlsm', office_bytes())
        before = source.read_bytes()
        inspected = self.office.inspect(source.name)
        self.assertTrue(inspected['macros_present'])
        self.office.edit(source.name, 'outputs/edited.xlsm', inspected['sha256'], cells=[
            {'sheet': 'Лист 1', 'cell': 'A1', 'value': 'Расходы ноября'},
            {'sheet': 'Лист 1', 'cell': 'B3', 'value': 42},
            {'sheet': 'Лист 1', 'cell': 'A2', 'value': '=malicious()'}])
        with ZipFile(source) as old, ZipFile(self.files.workspace_dir / 'outputs/edited.xlsm') as new:
            self.assertEqual(old.read('xl/vbaProject.bin'), new.read('xl/vbaProject.bin'))
            self.assertEqual(old.read('[Content_Types].xml'), new.read('[Content_Types].xml'))
            xml = new.read('xl/worksheets/sheet1.xml')
            self.assertIn(b'xmlns:x14ac=', xml)
            sheet = _xml(xml)
            a1 = sheet.find(f'.//{{{S}}}c[@r="A1"]')
            self.assertEqual(a1.get('s'), '3')
            self.assertIsNone(sheet.find(f'.//{{{S}}}c[@r="B1"]/{{{S}}}v'))
            self.assertEqual(sheet.find(f'.//{{{S}}}c[@r="A2"]').get('t'), 'inlineStr')
            self.assertEqual([r.get('r') for r in sheet.find(f'{{{S}}}sheetData')], ['1', '2', '3'])
        self.assertEqual(source.read_bytes(), before)

    def test_docm_replace_preserves_macro_binary_and_original_and_vba_source_is_text(self):
        source = self.save('report.docm', office_bytes('docm'))
        before = source.read_bytes()
        self.assertIn('Отчёт октября', extract_text(source))
        result = self.office.edit(source.name, 'outputs/report.docm', sha256(before).hexdigest(), replacements=[{'old': 'октября', 'new': 'ноября'}])
        target = self.files.workspace_dir / result['path']
        self.assertIn('ноября', extract_text(target))
        with ZipFile(source) as old, ZipFile(target) as new:
            self.assertEqual(old.read('word/vbaProject.bin'), new.read('word/vbaProject.bin'))
        self.assertEqual(source.read_bytes(), before)
        module = self.save('Module1.bas', 'Sub Main()\nMsgBox "тест"\nEnd Sub'.encode('utf-8'))
        self.assertIn('Sub Main', extract_text(module))

    def test_office_stale_ambiguous_and_extension_changes_fail_without_output(self):
        source = self.save('report.docm', office_bytes('docm'))
        for digest, output, replacements in [('0' * 64, 'outputs/x.docm', [{'old': 'октября', 'new': 'ноября'}]),
            (sha256(source.read_bytes()).hexdigest(), 'outputs/x.docx', [{'old': 'октября', 'new': 'ноября'}]),
            (sha256(source.read_bytes()).hexdigest(), 'outputs/x.docm', [{'old': 'not present', 'new': 'x'}])]:
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.office.edit(source.name, output, digest, replacements=replacements)
        self.assertFalse((self.files.workspace_dir / 'outputs/x.docm').exists())

    def test_compound_formula_ranges_are_preserved_and_regular_formula_is_not_silently_destroyed(self):
        original = office_bytes()
        rewritten = BytesIO()
        with ZipFile(BytesIO(original)) as old, ZipFile(rewritten, 'w') as new:
            for name in old.namelist():
                data = old.read(name)
                if name.endswith('sheet1.xml'):
                    data = data.replace(b'<f>SUM(B2:B3)</f>', b'<f t="array" ref="B1:B4">SUM(B2:B3)</f>')
                new.writestr(name, data)
        source = self.save('array.xlsm', rewritten.getvalue())
        for cell in ['B1', 'B3']:
            with self.subTest(cell=cell), self.assertRaises(ValueError):
                self.office.edit(source.name, 'outputs/array.xlsm', sha256(source.read_bytes()).hexdigest(), cells=[{'sheet': 'Лист 1', 'cell': cell, 'value': 1}])
        regular = self.save('regular.xlsm', original)
        with self.assertRaises(ValueError):
            self.office.edit(regular.name, 'outputs/regular.xlsm', sha256(original).hexdigest(), cells=[{'sheet': 'Лист 1', 'cell': 'B1', 'value': 1}])

    def test_merged_nonanchor_cells_fail_without_output_but_anchor_can_be_edited(self):
        sheet = f'<worksheet xmlns="{S}"><sheetData><row r="2"><c r="A2" t="inlineStr"><is><t>Заголовок</t></is></c></row></sheetData><mergeCells count="1"><mergeCell ref="A2:C3"/></mergeCells></worksheet>'
        content = office_with_parts({'xl/worksheets/sheet1.xml': sheet})
        source = self.save('merged.xlsm', content)
        digest = sha256(content).hexdigest()
        for address in ['B2', 'C3']:
            output = f'outputs/nonanchor-{address}.xlsm'
            with self.subTest(address=address), self.assertRaisesRegex(ValueError, 'верхнюю левую'):
                self.office.edit(source.name, output, digest, cells=[{'sheet': 'Лист 1', 'cell': address, 'value': 'Не должно быть записано'}])
            self.assertFalse((self.files.workspace_dir / output).exists())
            self.assertEqual(source.read_bytes(), content)
        result = self.office.edit(source.name, 'outputs/anchor.xlsm', digest,
            cells=[{'sheet': 'Лист 1', 'cell': 'A2', 'value': 'Новый заголовок'}])
        with ZipFile(self.files.workspace_dir / result['path']) as output:
            tree = _xml(output.read('xl/worksheets/sheet1.xml'))
            self.assertEqual(tree.find(f'.//{{{S}}}c[@r="A2"]/{{{S}}}is/{{{S}}}t').text, 'Новый заголовок')
            self.assertEqual(tree.find(f'.//{{{S}}}mergeCell').get('ref'), 'A2:C3')
        self.assertEqual(source.read_bytes(), content)

    def test_excel_replacements_refuse_shared_string_side_effects_and_cells_edit_only_target(self):
        sheet = f'<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>0</v></c></row></sheetData></worksheet>'
        strings = f'<sst xmlns="{S}" count="2" uniqueCount="1"><si><t>Общая строка</t></si></sst>'
        content = office_with_parts({'xl/worksheets/sheet1.xml': sheet, 'xl/sharedStrings.xml': strings})
        for suffix in ['xlsx', 'xlsm', 'xltm']:
            source = self.save('shared.' + suffix, content)
            output = 'outputs/shared.' + suffix
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, r'cells\[\]'):
                self.office.edit(source.name, output, sha256(content).hexdigest(),
                    replacements=[{'old': 'Общая строка', 'new': 'Случайно изменяет обе ячейки'}])
            self.assertFalse((self.files.workspace_dir / output).exists())
            self.assertEqual(source.read_bytes(), content)
        result = self.office.edit('shared.xlsm', 'outputs/single-cell.xlsm', sha256(content).hexdigest(),
            cells=[{'sheet': 'Лист 1', 'cell': 'A1', 'value': 'Только первая ячейка'}])
        with ZipFile(self.files.workspace_dir / result['path']) as output:
            tree = _xml(output.read('xl/worksheets/sheet1.xml'))
            self.assertEqual(tree.find(f'.//{{{S}}}c[@r="A1"]/{{{S}}}is/{{{S}}}t').text, 'Только первая ячейка')
            self.assertEqual(tree.find(f'.//{{{S}}}c[@r="B1"]').get('t'), 's')
            self.assertEqual(tree.find(f'.//{{{S}}}c[@r="B1"]/{{{S}}}v').text, '0')
            self.assertEqual(output.read('xl/sharedStrings.xml'), strings.encode())
            self.assertEqual(output.read('xl/vbaProject.bin'), b'opaque-vba-fixture\x00\xff')

    def test_calc_properties_insert_before_following_elements_without_reordering_package(self):
        with ZipFile(BytesIO(office_bytes())) as original:
            workbook = original.read('xl/workbook.xml')
        followers = ['oleSize', 'customWorkbookViews', 'pivotCaches', 'smartTagPr', 'smartTagTypes',
                     'webPublishing', 'fileRecoveryPr', 'webPublishObjects', 'extLst']
        for follower in followers:
            modified = workbook.replace(b'</workbook>', f'<{follower}/></workbook>'.encode())
            content = office_with_parts({'xl/workbook.xml': modified})
            source = self.save(f'order-{follower}.xlsm', content)
            result = self.office.edit(source.name, f'outputs/order-{follower}.xlsm', sha256(content).hexdigest(),
                cells=[{'sheet': 'Лист 1', 'cell': 'A1', 'value': 'Проверка порядка'}])
            with self.subTest(follower=follower), ZipFile(source) as before, ZipFile(self.files.workspace_dir / result['path']) as after:
                tree = _xml(after.read('xl/workbook.xml'))
                self.assertEqual([node.tag for node in tree], [f'{{{S}}}sheets', f'{{{S}}}calcPr', f'{{{S}}}{follower}'])
                self.assertEqual(tree.find(f'{{{S}}}calcPr').get('fullCalcOnLoad'), '1')
                self.assertEqual(before.namelist(), after.namelist())
                for name in before.namelist():
                    if name not in result['changed_parts']:
                        self.assertEqual(before.read(name), after.read(name), name)
            self.assertEqual(source.read_bytes(), content)

    def test_value_edit_preserves_calc_chain_and_formulas_while_invalidating_cached_values(self):
        chain = f'<calcChain xmlns="{S}"><c r="B1" i="1"/></calcChain>'.encode()
        content = office_with_parts({'xl/calcChain.xml': chain})
        source = self.save('chain.xlsm', content)
        digest = sha256(content).hexdigest()
        with self.assertRaisesRegex(ValueError, 'Изменение формул'):
            self.office.edit(source.name, 'outputs/replaced-formula.xlsm', digest,
                cells=[{'sheet': 'Лист 1', 'cell': 'B1', 'value': 2}])
        self.assertFalse((self.files.workspace_dir / 'outputs/replaced-formula.xlsm').exists())
        result = self.office.edit(source.name, 'outputs/input-edited.xlsm', digest,
            cells=[{'sheet': 'Лист 1', 'cell': 'B2', 'value': 42}])
        with ZipFile(self.files.workspace_dir / result['path']) as output:
            self.assertEqual(output.read('xl/calcChain.xml'), chain)
            tree = _xml(output.read('xl/worksheets/sheet1.xml'))
            self.assertEqual(tree.find(f'.//{{{S}}}c[@r="B1"]/{{{S}}}f').text, 'SUM(B2:B3)')
            self.assertIsNone(tree.find(f'.//{{{S}}}c[@r="B1"]/{{{S}}}v'))
            self.assertEqual(output.read('xl/vbaProject.bin'), b'opaque-vba-fixture\x00\xff')
        self.assertEqual(source.read_bytes(), content)

    def test_palette_alpha_and_vision_exif_are_not_lost_or_uploaded(self):
        image = Image.new('P', (20, 20), 0)
        image.putpalette([255, 0, 0] * 256)
        content = BytesIO()
        image.save(content, 'PNG', transparency=0)
        self.save('palette.png', content.getvalue())
        result = self.images.transform('palette.png', 'outputs/palette.png')
        with Image.open(self.files.workspace_dir / result['path']) as actual:
            self.assertEqual(actual.convert('RGBA').getpixel((0, 0))[3], 0)
        exif = Image.Exif()
        exif[0x010e] = 'PRIVATE-METADATA-FIXTURE'
        buffer = BytesIO()
        Image.new('RGB', (20, 20), 'red').save(buffer, 'JPEG', exif=exif)
        self.save('metadata.jpg', buffer.getvalue())
        captured = []
        class Vision:
            name, model = 'fixture', 'test'
            def analyze(self, data, mime, prompt):
                captured.append(data)
                return 'red'
        VisionRouter(self.files.workspace_dir, [Vision()]).analyze_path('metadata.jpg', 'colour')
        self.assertNotIn(b'PRIVATE-METADATA-FIXTURE', captured[0])

    def test_preview_avatar_auth_tenant_cropping_and_delete(self):
        service = AgentService.__new__(AgentService)
        service.root_dir = self.root
        service.settings = replace(Settings.load(ROOT), database_file=self.root / 'qa.sqlite', memory_file=self.root / 'memory.json', tool_workspace_dir=self.root / 'users', model_provider='mock', model_name='mock-local', reasoning_fallback_enabled=False)
        service.database = AvelinDatabase(service.settings.database_file)
        service._agents, service._connector_factories = {}, []
        with patch('backend.routes.agent_service', service):
            client = TestClient(app)
            def register(name):
                token = client.post('/api/auth/register', json={'email': name + '@test.example', 'username': name, 'password': 'password123', 'display_name': name}).json()['token']
                return {'Authorization': 'Bearer ' + token, 'X-Filename': 'photo.heic'}
            a, b = register('media-owner'), register('media-other')
            self.assertEqual(client.get('/api/profile/avatar').status_code, 401)
            self.assertEqual(client.get('/api/profile/avatar', headers=a).status_code, 404)
            data = image_bytes('HEIF')
            preview = client.post('/api/profile/avatar/preview', headers=a, content=data)
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(Image.open(BytesIO(preview.content)).format, 'JPEG')
            response = client.put('/api/profile/avatar', headers=a, content=data)
            self.assertEqual(response.status_code, 200, response.text)
            avatar = client.get('/api/profile/avatar', headers=a)
            self.assertEqual(Image.open(BytesIO(avatar.content)).size, (256, 256))
            self.assertEqual(client.get('/api/profile/avatar', headers=b).status_code, 404)
            uploaded = client.post('/api/attachments', headers=a, content=data).json()
            self.assertEqual(client.get('/api/workspace/preview', headers=a, params={'path': uploaded['path']}).status_code, 200)
            self.assertEqual(client.get('/api/workspace/preview', headers=b, params={'path': uploaded['path']}).status_code, 404)
            self.assertEqual(client.get('/api/workspace/preview', headers=a, params={'path': '../other.png'}).status_code, 404)
            self.assertEqual(client.delete('/api/profile/avatar', headers=a).status_code, 200)
            self.assertEqual(client.get('/api/profile/avatar', headers=a).status_code, 404)


if __name__ == '__main__':
    unittest.main()
