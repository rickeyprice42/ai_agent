"""Bounded, synthetic format fixtures; never accesses the user's documents."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED
import unittest

from backend.attachments import extract_text, validate_attachment


def office_package(entries: dict[str, str]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as package:
        for name, content in entries.items():
            package.writestr(name, content)
    return output.getvalue()


class AttachmentFormatTests(unittest.TestCase):
    def extract(self, name: str, content: bytes) -> str:
        validate_attachment(name, content)
        with TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_bytes(content)
            return extract_text(path)

    def test_docx_cyrillic_paragraph_and_table_text_survive_upload(self) -> None:
        body = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Расходы за октябрь</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Итого 4250</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
        text = self.extract("report.docx", office_package({"word/document.xml": body}))
        self.assertIn("Расходы за октябрь", text)
        self.assertIn("Итого 4250", text)

    def test_xlsx_preserves_cells_and_formula_without_claiming_recalculation(self) -> None:
        text = self.extract("budget.xlsx", office_package({
            "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
            "xl/sharedStrings.xml": '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Расходы</t></si></sst>',
            "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><f>SUM(B2:B4)</f><v>0</v></c></row></sheetData></worksheet>',
        }))
        self.assertIn("Расходы", text)
        self.assertIn("B1", text)
        self.assertIn("SUM(B2:B4)", text)

    def test_pptx_contains_slide_text(self) -> None:
        text = self.extract("slides.pptx", office_package({
            "ppt/presentation.xml": '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
            "ppt/slides/slide1.xml": '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>Первый результат</a:t></p:sld>',
        }))
        self.assertIn("Первый результат", text)

    def test_pdf_extracts_real_text_from_a_native_pdf_page(self) -> None:
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=400, height=300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 40 200 Td (Invoice total 4250) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(content)
        buffer = BytesIO()
        writer.write(buffer)
        self.assertIn("Invoice total 4250", self.extract("invoice.pdf", buffer.getvalue()))

    def test_pdf_without_text_has_honest_ocr_fallback(self) -> None:
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=400, height=300)
        buffer = BytesIO()
        writer.write(buffer)
        self.assertIn("OCR", self.extract("scan.pdf", buffer.getvalue()))

    def test_large_decompressed_office_upload_is_rejected_before_extraction(self) -> None:
        package = office_package({"word/document.xml": "A" * (41 * 1024 * 1024)})
        self.assertLess(len(package), 10 * 1024 * 1024)
        with self.assertRaises(ValueError):
            validate_attachment("compressed.docx", package)


if __name__ == "__main__":
    unittest.main()
