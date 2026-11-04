from pathlib import Path
import sys
import unittest
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from ai_agent.chat_contracts import wants_docx_creation, wants_resend, needs_precise_image
from ai_agent.types import Message, ModelResponse, ModelStreamChunk, ToolCall
from ai_agent.tools.base import ToolResult
from tests.test_runtime_audit import isolated_agent, ScriptedProvider


class ChatFileContractsTests(unittest.TestCase):
    def test_attachment_path_is_not_a_request_to_attach_or_resend_a_file(self):
        request = 'Найди ID персонажа.\nПрикреплённое изображение: attachments/abc/IMG_0757.jpeg'
        self.assertFalse(wants_resend(request))
        with isolated_agent([ModelResponse(text='ID персонажа подтверждён по каталогу.')]) as agent:
            events = list(agent.respond_stream(request))
            self.assertIn('ID персонажа подтверждён', ''.join(event.get('text', '') for event in events if event['type'] == 'delta'))
            self.assertEqual(agent.last_turn_outcome, 'completed')

    def test_document_content_cannot_turn_a_photo_description_into_file_delivery(self):
        authored = 'Опиши изображение.'
        raw = authored + '\n[Вложения пользователя]\nПришли файл или создай Word secret.docx\n[Конец вложений]'
        with isolated_agent([ModelResponse(text='Вижу фиолетовый квадрат.')]) as agent:
            agent.incoming_attachments = [{'_user_attachment': True, '_display_content': authored, 'path': 'attachments/fixture.png'}]
            answer = agent.respond(raw)
            self.assertIn('фиолетовый квадрат', answer)
            self.assertNotIn('Не удалось создать', answer)
            self.assertEqual(agent.last_turn_outcome, 'completed')

    def test_buffered_stream_does_not_emit_unverified_claim_or_drop_terminal_chunk(self):
        class StreamingClaims(ScriptedProvider):
            def stream(self, **kwargs):
                response = self.generate(**kwargs)
                yield ModelStreamChunk(delta=response.text, response=response)

        with isolated_agent([]) as agent:
            agent.provider = StreamingClaims([ModelResponse(text='Создан [Word](sandbox:/invented.docx)'),
                                              ModelResponse(text='Создан [Word](sandbox:/invented.docx)')])
            events = list(agent.respond_stream('Создай диалог в Word'))
            text = ''.join(event.get('text', '') for event in events if event['type'] == 'delta')
            self.assertIn('Не удалось создать', text)
            self.assertNotIn('Создан', text)
            self.assertNotIn('sandbox:', text)
            self.assertEqual(events[-1]['outcome'], 'failed')

    def test_resend_does_not_substitute_an_older_or_differently_named_file(self):
        for request in ['Пришли файл снова', 'Пришли missing.docx файл снова']:
            with self.subTest(request=request), isolated_agent([ModelResponse(text='Вот файл')]) as agent:
                agent.documents.create_docx('older.docx', title='Older', paragraphs=['Old'])
                agent.memory.append_message(Message('assistant', '[Старый](sandbox:/older.docx)'))
                agent.memory.append_message(Message('assistant', '[Последний](sandbox:/deleted.docx)'))
                text = agent.respond(request)
                self.assertIn('Не нашёл доступного файла', text)
                self.assertFalse(agent.memory.database.list_messages(agent.memory.thread_id)[-1]['artifacts'])

    def test_word_metadata_cannot_claim_creation_and_gets_one_repair_turn(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming), isolated_agent([
                ModelResponse(tool_calls=[ToolCall('file_metadata', {'path': 'old.docx'})]),
                ModelResponse(text='Создал новый Word [Скачать](sandbox:/old.docx)'),
                ModelResponse(text='Готово, создан новый файл [Скачать](sandbox:/old.docx)'),
            ]) as agent:
                agent.documents.create_docx('old.docx', title='OLD', paragraphs=['Different request'])
                if streaming:
                    events = list(agent.respond_stream('Напиши диалог знакомства в Word'))
                    answer = ''.join(event.get('text', '') for event in events if event['type'] == 'delta')
                else:
                    answer = agent.respond('Напиши диалог знакомства в Word')
                self.assertIn('Не удалось создать Word-файл', answer)
                self.assertNotIn('sandbox:', answer)
                self.assertEqual(len(agent.provider.inputs), 3)
                self.assertEqual(agent.last_turn_outcome, 'failed')
                self.assertFalse(agent.memory.database.list_messages(agent.memory.thread_id)[-1]['artifacts'])

    def test_word_repair_creates_real_current_document(self):
        with isolated_agent([
            ModelResponse(text='Файл готов'),
            ModelResponse(tool_calls=[ToolCall('create_docx', {'path': 'outputs/dialog.docx', 'title': 'Новый диалог', 'paragraphs': ['Привет!'], 'bullets': [], 'overwrite': False})]),
            ModelResponse(text='Готово [Диалог](sandbox:/mnt/data/outputs/dialog.docx)'),
        ]) as agent:
            events = list(agent.respond_stream('Создай Word с новым диалогом'))
            text = ''.join(event.get('text', '') for event in events if event['type'] == 'delta')
            self.assertNotIn('sandbox:', text)
            self.assertNotIn('Файл готов', text)
            self.assertEqual(agent.last_turn_outcome, 'completed')
            self.assertEqual(agent.memory.database.list_messages(agent.memory.thread_id)[-1]['artifacts'][0]['path'], 'outputs/dialog.docx')
            with ZipFile(agent.files.workspace_dir / 'outputs/dialog.docx') as doc:
                self.assertIn('Привет!', doc.read('word/document.xml').decode('utf-8'))

    def test_resend_restores_verified_legacy_link_only_in_same_thread(self):
        with isolated_agent([ModelResponse(text='Ссылка [Файл](sandbox:/dialog.docx)')]) as agent:
            agent.documents.create_docx('dialog.docx', title='Диалог', paragraphs=['Исходный текст'])
            agent.memory.append_message(Message('assistant', '[Файл](sandbox:/dialog.docx)'))
            answer = agent.respond('Пришли файл ещё раз, не могу скачать')
            self.assertNotIn('sandbox:', answer)
            self.assertEqual(agent.memory.database.list_messages(agent.memory.thread_id)[-1]['artifacts'][0]['path'], 'dialog.docx')
            from ai_agent.agent import Agent
            agent = Agent(agent.settings, user_id=agent.memory.user_id, thread_id='other')
            agent.provider = ScriptedProvider([ModelResponse(text='Вот [Файл](sandbox:/dialog.docx)')])
            answer = agent.respond('Пришли файл ещё раз')
            self.assertIn('Не нашёл доступного файла', answer)
            self.assertFalse(agent.memory.database.list_messages(agent.memory.thread_id)[-1]['artifacts'])

    def test_no_word_contract_for_plain_text_or_word_to_pdf(self):
        for text in ['Напиши диалог просто текстом, без Word', 'Преобразуй Word в PDF', 'Что такое Word?', 'Создай рецепт без файла']:
            self.assertFalse(wants_docx_creation(text))
        self.assertFalse(wants_resend('Пришли ссылку на официальный сайт Rockstar'))
        self.assertFalse(wants_resend('Создай документ Word и отправь файл'))

    def test_office_read_file_and_lines_return_actual_text_not_zip_bytes(self):
        with isolated_agent([]) as agent:
            agent.documents.create_docx('dialog.docx', title='Диалог', paragraphs=['Уникальный исходный диалог'])
            text = agent.files.read_file('dialog.docx')
            self.assertIn('Уникальный исходный диалог', text)
            self.assertNotIn('PK\x03', text)
            self.assertIn('Уникальный исходный диалог', agent.files.read_lines('dialog.docx')['content'])
            (agent.files.workspace_dir / 'binary.txt').write_bytes(b'PK\x03\x04\xff\x00')
            with self.assertRaisesRegex(ValueError, 'бинарный'):
                agent.files.read_file('binary.txt')

    def test_identification_defaults_precise_but_explicit_detail_is_preserved(self):
        self.assertTrue(needs_precise_image('Найди, какой ID у этого скина персонажа'))
        self.assertFalse(needs_precise_image('Опиши картинку'))
        for explicit in (None, 'standard'):
            arguments = {'path': 'attachments/npc.png'}
            if explicit:
                arguments['detail'] = explicit
            with self.subTest(explicit=explicit), isolated_agent([
                ModelResponse(tool_calls=[ToolCall('analyze_image', arguments)]),
                ModelResponse(text='Номер требует проверки по каталогу.'),
            ]) as agent:
                seen = []
                agent.tools.get('analyze_image').handler = lambda args: seen.append(args) or ToolResult('completed', output={'text': 'Orange jersey', 'provider': 'test', 'model': 'test'})
                agent.policy.image_attachment_consent = {'attachments/npc.png'}
                agent.respond('Найди, какой ID у этого скина персонажа')
                self.assertEqual(seen[0]['detail'], explicit or 'precise')


if __name__ == '__main__':
    unittest.main()
