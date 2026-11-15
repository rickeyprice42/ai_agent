"""Stateless file handlers reconstructed in a fresh, contained worker."""
from dataclasses import replace
from pathlib import Path
import sys

from ai_agent.tools.files import FileSandbox
from ai_agent.tools.process_host import HostedHandler, ProcessHandlerSpec

HOSTED_FILE_TOOLS = frozenset({'read_file','read_file_lines','read_pdf_pages','file_metadata','write_file',
    'create_docx','append_docx','create_presentation',
    'list_workspace_files','search_workspace','edit_file_exact','inspect_image','transform_image',
    'inspect_office_document','edit_office_document'})


class HostedFileHandler(HostedHandler):
    def __init__(self, spec, files, *, timeout_seconds=30, documents=None, presentations=None):
        super().__init__(spec,timeout_seconds=timeout_seconds)
        self.files = files
        self.documents,self.presentations = documents,presentations

    def restricted_to(self, workspace, subpath, *, view_root=''):
        return HostedFileHandler(replace(self.spec,workspace=Path(workspace),workspace_subpath=subpath,
            configuration={**self.spec.configuration,'view_root':view_root}),self.files,timeout_seconds=self.timeout_seconds,
            documents=self.documents,presentations=self.presentations)

    def __call__(self, arguments, context=None):
        # Runtime read limits may change between turns; snapshot values rather
        # than serializing the live FileSandbox or retaining a stale limit.
        spec = replace(self.spec,configuration={**self.spec.configuration,
            'max_read_chars':self.files.max_read_chars,'max_write_chars':self.files.max_write_chars,
            'document_limit':self.documents.max_text_chars if self.documents else 20000,
            'presentation_limit':self.presentations.max_text_chars if self.presentations else 30000,
            'max_slides':self.presentations.max_slides if self.presentations else 30})
        return HostedHandler(spec,timeout_seconds=self.timeout_seconds)(arguments,context)


def enable_file_process_hosts(registry, files, documents=None, presentations=None):
    if sys.platform != 'linux':return
    for name in HOSTED_FILE_TOOLS:
        tool = registry.get(name)
        tool.handler = HostedFileHandler(ProcessHandlerSpec(
            entrypoint='ai_agent.tools.hosted_files:call_file_tool',workspace=files.workspace_dir,
            configuration={'name':name},expose_value_errors=True,validation_errors_safe=True,
            read_only_workspace=all(permission.risk=='read' for permission in tool.permissions)),files,timeout_seconds=tool.timeout_seconds,
            documents=documents,presentations=presentations)


def call_file_tool(arguments, configuration):
    root = Path('/workspace') / configuration.get('view_root','')
    files = FileSandbox(root,configuration.get('max_read_chars',20000),configuration.get('max_write_chars',20000))
    name = configuration['name']
    a = arguments
    if name=='read_file':return files.read_file(a['path'])
    if name=='write_file':return files.write_file(a['path'],a['content'],a.get('overwrite',False))
    if name in {'create_docx','append_docx'}:
        from ai_agent.tools.documents import DocumentSandbox
        documents = DocumentSandbox(root,configuration.get('document_limit',20000))
        values = {**a,'relative_path':a['path']}
        values.pop('path')
        return documents.create_docx(**values) if name=='create_docx' else documents.append_docx(**values)
    if name=='create_presentation':
        from ai_agent.tools.presentations import PresentationSandbox
        presentations = PresentationSandbox(root,configuration.get('presentation_limit',30000),configuration.get('max_slides',30))
        values = {**a,'relative_path':a['path']}
        values.pop('path')
        return presentations.create_presentation(**values)
    if name=='read_file_lines':return files.read_lines(a['path'],a.get('start_line',1),a.get('end_line'),a.get('start_column',1))
    if name=='read_pdf_pages':return files.read_pdf_pages(a['path'],a.get('start_page',1),a.get('end_page'),a.get('start_character',1))
    if name=='file_metadata':return files.metadata(a['path'])
    if name=='list_workspace_files':return files.format_file_list(a.get('limit',30))
    if name=='search_workspace':return files.search(a['query'],a.get('path',''),include_content=a.get('include_content',True),limit=a.get('limit',50))
    if name=='edit_file_exact':return files.exact_edit(a['path'],a['expected_sha256'],a['old_text'],a['new_text'])
    if name in {'inspect_image','transform_image'}:
        from ai_agent.tools.media import ImageSandbox
        images = ImageSandbox(files)
        return images.inspect(a['path']) if name=='inspect_image' else images.transform(**a)
    if name in {'inspect_office_document','edit_office_document'}:
        from ai_agent.tools.office_files import OfficeSandbox
        office = OfficeSandbox(files)
        return office.inspect(a['path']) if name=='inspect_office_document' else office.edit(**a)
    raise ValueError('Unknown hosted file tool')
