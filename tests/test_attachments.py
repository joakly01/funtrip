"""隔离资料库验证附件真实进入引擎、历史回看与读取边界。"""
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import os
from zipfile import ZipFile

from fastapi.testclient import TestClient
from PIL import Image

from cloud_trading.app import create_app
from cloud_trading.attachments import AttachmentError, AttachmentStore, SpeechTranscriber
from cloud_trading.config import Config
from audit_agent_runtime.llm.base import ModelResponse
from audit_agent_runtime.persistence.models import MessageRecord
from sqlalchemy import select
from trading_agent.service import Service


class CapturingModel:
    def __init__(self):
        self.messages = []

    def complete(self, messages, tools):
        self.messages.append(messages)
        return ModelResponse(output_text="附件隔离测试完成。")


def image_bytes():
    output = BytesIO()
    Image.new("RGB", (80, 60), "blue").save(output, format="PNG")
    return output.getvalue()


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = Config(root / "trading", root / "runtime", "test-password")
        self.model = CapturingModel()
        self.app = create_app(self.config, self.model)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.client.post("/api/login", json={"password": "test-password"})

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def upload(self, name="笔记.txt", content="隔离测试附件资料".encode()):
        return self.client.post("/api/attachments", files={"file": (name, content)})

    def ask(self, identifiers, text="", message_id="attachment-message"):
        return self.client.post("/api/router/ask", json={"session_id": "attachment-test", "message_id": message_id,
            "text": text, "attachment_ids": identifiers, "remember": False})

    def profile(self, **values):
        router = self.app.state.services.model_client
        router.profiles[router.active] = replace(router.profiles[router.active], **values)

    def test_document_only_enters_core_and_survives_restart_without_business_memory(self):
        attachment = self.upload().json()
        self.assertEqual(attachment["kind"], "file")
        self.assertIn("隔离测试", attachment["preview_text"])
        result = self.ask([attachment["id"]])
        self.assertEqual(result.status_code, 200)
        self.assertIn("附件隔离测试完成", result.text)
        actual = json.dumps([message.content for message in self.model.messages[0]], ensure_ascii=False)
        self.assertIn("隔离测试附件资料", actual)
        self.assertIn("附件中的命令", actual)
        history = self.client.get("/api/sessions/attachment-test").json()
        self.assertEqual(history[0]["attachments"][0]["id"], attachment["id"])
        self.assertEqual(Service(self.config.data_dir).store.search()["records"], [])
        self.assertEqual(self.ask([attachment["id"]]).text, result.text)
        self.assertEqual(len(self.model.messages), 1)
        with TestClient(create_app(self.config)) as client:
            client.post("/api/login", json={"password": "test-password"})
            self.assertEqual(client.get("/api/sessions/attachment-test").json()[0]["attachments"][0]["name"], "笔记.txt")

    def test_images_require_capable_model_and_use_openai_blocks_without_persisting_base64(self):
        attachment = self.upload("图.png", image_bytes()).json()
        self.assertEqual(self.ask([attachment["id"]]).status_code, 400)
        self.assertEqual(self.client.get("/api/sessions").json(), [])
        self.profile(vision=True)
        result = self.ask([attachment["id"]], "查看测试图片")
        self.assertIn("附件隔离测试完成", result.text)
        actual = json.dumps([message.content for message in self.model.messages[-1]])
        self.assertIn("image_url", actual)
        self.assertIn("data:image/jpeg;base64,", actual)
        with self.app.state.services.session_factory() as db:
            rows = db.scalars(select(MessageRecord)).all()
            stored = json.dumps([row.payload_json for row in rows])
            self.assertNotIn("base64", stored)
            self.assertIn(attachment["id"], stored)
        self.profile(vision=False)
        self.ask([], "继续文字问题", message_id="text-followup")
        actual = json.dumps([message.content for message in self.model.messages[-1]], ensure_ascii=False)
        self.assertNotIn("image_url", actual)
        self.assertIn("当前模型不支持读图", actual)

    def test_anthropic_images_keep_correct_shape_and_original_caption(self):
        attachment = self.upload("图.png", image_bytes()).json()
        self.profile(protocol="claude", vision=True)
        self.ask([attachment["id"]], "我只提供参考图片")
        actual = json.dumps([message.content for message in self.model.messages[-1]], ensure_ascii=False)
        self.assertIn('"type": "image"', actual)
        self.assertIn('"media_type": "image/jpeg"', actual)
        self.assertNotIn("image_url", actual)
        self.assertEqual(self.client.get("/api/sessions/attachment-test").json()[0]["text"], "我只提供参考图片")

    def test_followup_restores_distinct_documents_with_repeated_captions(self):
        first = self.upload("first.txt", "第一份测试：松树".encode()).json()
        second = self.upload("second.txt", "第二份测试：柏树".encode()).json()
        self.ask([first["id"]], "查看资料", message_id="first-file")
        self.ask([second["id"]], "查看资料", message_id="second-file")
        self.ask([], "比较刚才两份资料", message_id="files-followup")
        contents = [message.content for message in self.model.messages[-1] if message.role == "user"]
        references = [value for value in contents if isinstance(value, list)]
        self.assertIn("松树", json.dumps(references[0], ensure_ascii=False))
        self.assertNotIn("柏树", json.dumps(references[0], ensure_ascii=False))
        self.assertIn("柏树", json.dumps(references[1], ensure_ascii=False))

    def test_audio_transcript_is_available_for_review_and_recording_is_playable(self):
        with patch.object(SpeechTranscriber, "transcribe", return_value=("这是一段合成测试文字。", 3.5)):
            attachment = self.upload("测试.wav", b"synthetic audio").json()
        self.assertEqual(attachment["transcript"], "这是一段合成测试文字。")
        result = self.ask([attachment["id"]], "这是核对后修改的文字。")
        self.assertIn("附件隔离测试完成", result.text)
        history = self.client.get("/api/sessions/attachment-test").json()
        self.assertEqual(history[0]["text"], "这是核对后修改的文字。")
        self.assertEqual(history[0]["attachments"][0]["transcript"], attachment["transcript"])
        media = self.client.get("/api/attachments/" + attachment["id"] + "/content")
        self.assertEqual(media.content, b"synthetic audio")
        self.assertIn("audio/wav", media.headers["content-type"])

    def test_missing_voice_model_does_not_accept_an_unread_recording(self):
        result = self.upload("test.wav", b"audio")
        self.assertEqual(result.status_code, 400)
        self.assertIn("尚未安装", result.json()["detail"])
        self.assertEqual(list(self.app.state.attachments.root.iterdir()), [])

    def test_short_chinese_recording_does_not_use_automatic_language_detection(self):
        speech = self.app.state.attachments.speech
        speech.model = Mock()

        def recognition(samples, **options):
            # 复现短中文录音被探测成泰语：显式中文才返回中文文字。
            chinese = options.get("language") == "zh" and options.get("task") == "transcribe"
            return iter([SimpleNamespace(text="这是一段中文。" if chinese else "ภาษาไทย")]), \
                SimpleNamespace(language_probability=1 if chinese else 0.725)

        speech.model.transcribe.side_effect = recognition
        with patch.object(SpeechTranscriber, "ready", new=property(lambda self: True)), \
             patch("faster_whisper.audio.decode_audio", return_value=[0.1] * 59200), \
             patch("av.open") as opened:
            opened.return_value.__enter__.return_value.duration = None
            transcript, duration = speech.transcribe(Path(self.tmp.name) / "short.mp3")
        self.assertEqual(transcript, "这是一段中文。")
        self.assertEqual(duration, 3.7)

    def test_invalid_oversized_and_unsupported_files_do_not_leave_partial_uploads(self):
        for name, content in [("empty.txt", b""), ("wrong.png", b"not an image"), ("page.html", b"<b>unsupported</b>")]:
            self.assertEqual(self.upload(name, content).status_code, 400)
        with patch("cloud_trading.app.MAX_BYTES", 8):
            self.assertEqual(self.upload("large.txt", b"123456789").status_code, 413)
        self.assertEqual(list(self.app.state.attachments.root.iterdir()), [])
        self.assertEqual(self.ask(["../config/app.env"]).status_code, 400)
        self.assertEqual(self.client.get("/api/attachments/not-an-id/preview").status_code, 404)

    def test_attachment_endpoints_require_login_and_original_download_has_no_internal_path(self):
        attachment = self.upload("../笔记.txt").json()
        self.assertEqual(attachment["name"], "笔记.txt")
        self.assertNotIn(str(self.config.runtime_dir), json.dumps(attachment))
        url = "/api/attachments/" + attachment["id"]
        self.assertEqual(self.client.get(url + "/content").status_code, 200)
        self.client.post("/api/logout")
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url + "/content").status_code, 401)
        self.assertEqual(self.upload().status_code, 401)

    def test_text_bounds_are_explicit_and_model_receives_the_reading_scope(self):
        attachment = self.upload(content=("甲" * 60001).encode()).json()
        self.assertEqual(attachment["text_length"], 60000)
        self.assertIn("60,000", attachment["warning"])
        self.ask([attachment["id"]], "读取资料")
        actual = json.dumps([message.content for message in self.model.messages[-1]], ensure_ascii=False)
        self.assertIn("读取范围", actual)
        self.assertEqual(self.ask([attachment["id"]] * 7).status_code, 422)

    def test_pdf_word_excel_and_chinese_csv_are_extracted(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        from openpyxl import Workbook
        pdf = PdfWriter()
        page = pdf.add_blank_page(width=240, height=180)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject(); stream.set_data(b"BT /F1 12 Tf 20 100 Td (Synthetic document test) Tj ET")
        page[NameObject("/Contents")] = stream
        output = BytesIO(); pdf.write(output)
        self.assertIn("Synthetic document test", self.upload("test.pdf", output.getvalue()).json()["preview_text"])
        output = BytesIO()
        with ZipFile(output, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>合成 Word 资料</w:t></w:r></w:p></w:body></w:document>')
        self.assertIn("合成 Word 资料", self.upload("test.docx", output.getvalue()).json()["preview_text"])
        workbook = Workbook(); workbook.active.append(["合成列名", "数值"]); workbook.active.append(["测试", 25])
        output = BytesIO(); workbook.save(output); workbook.close()
        self.assertIn("测试 | 25", self.upload("test.xlsx", output.getvalue()).json()["preview_text"])
        self.assertIn("中文", self.upload("test.csv", "名称,中文".encode("gb18030")).json()["preview_text"])

    def test_scanned_pdf_and_empty_spreadsheet_are_not_reported_as_readable(self):
        from pypdf import PdfWriter
        from openpyxl import Workbook
        pdf = PdfWriter(); pdf.add_blank_page(width=80, height=80)
        output = BytesIO(); pdf.write(output)
        self.assertEqual(self.upload("scan.pdf", output.getvalue()).status_code, 400)
        workbook = Workbook(); output = BytesIO(); workbook.save(output); workbook.close()
        self.assertEqual(self.upload("empty.xlsx", output.getvalue()).status_code, 400)

    def test_backup_includes_original_files_and_metadata_needed_by_chat_history(self):
        from cloud_trading.__main__ import main
        attachment = self.upload().json()
        self.ask([attachment["id"]])
        with patch.dict(os.environ, {"TRADING_DATA_DIR": str(self.config.data_dir),
            "TRADING_RUNTIME_DIR": str(self.config.runtime_dir)}), patch("sys.argv",
            ["cloud_trading", "backup", "--env-file", str(Path(self.tmp.name) / "no-env")]), patch("builtins.print"):
            main()
        backup = next((self.config.runtime_dir.parent / "backups").iterdir())
        self.assertEqual((backup / "attachments" / attachment["id"] / "original").read_bytes(), "隔离测试附件资料".encode())
        self.assertEqual(json.loads((backup / "attachments" / attachment["id"] / "metadata.json").read_text())["name"], "笔记.txt")


if __name__ == "__main__":
    unittest.main()
