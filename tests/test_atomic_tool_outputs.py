from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_agent.tools.atomic_files import atomic_output
from ai_agent.tools.documents import DocumentSandbox
from ai_agent.tools.presentations import PresentationSandbox


class AtomicToolOutputTests(unittest.TestCase):
    def test_failed_docx_build_preserves_original_package_and_cleans_staging(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            documents = DocumentSandbox(root)
            documents.create_docx('report.docx',paragraphs=['Original'])
            original = (root/'report.docx').read_bytes()
            def partial(target,**kwargs):
                target.write_bytes(b'PARTIAL')
                raise RuntimeError('Synthetic writer failure')
            with patch('ai_agent.tools.documents._write_minimal_docx',side_effect=partial):
                with self.assertRaises(RuntimeError):documents.create_docx('report.docx',paragraphs=['New'],overwrite=True)
            self.assertEqual((root/'report.docx').read_bytes(),original)
            with ZipFile(root/'report.docx') as package:self.assertIsNone(package.testzip())
            self.assertEqual(list(root.glob('*.pending')),[])

    def test_pptx_validation_failure_does_not_replace_existing_target(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/'slides.pptx'
            target.write_bytes(b'ORIGINAL')
            with patch('ai_agent.tools.presentations._validate_pptx_package',side_effect=ValueError('Invalid staged package')):
                with self.assertRaises(ValueError):PresentationSandbox(root).create_presentation('slides.pptx',title='Fixture',overwrite=True)
            self.assertEqual(target.read_bytes(),b'ORIGINAL')
            self.assertEqual(list(root.glob('*.pending')),[])

    def test_no_overwrite_publication_cannot_clobber_concurrent_output(self):
        with TemporaryDirectory() as directory:
            target = Path(directory)/'output.txt'
            with self.assertRaises(ValueError):
                with atomic_output(target,overwrite=False) as temporary:
                    temporary.write_text('MY OUTPUT')
                    target.write_text('CONCURRENT OUTPUT')
            self.assertEqual(target.read_text(),'CONCURRENT OUTPUT')
            self.assertEqual(list(target.parent.glob('*.pending')),[])


if __name__=='__main__':unittest.main()
