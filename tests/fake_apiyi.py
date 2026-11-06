"""A local HTTP server that stands in for APIYI, so tests see the real requests and spend no money."""

from __future__ import annotations

import base64
import json
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from typing import Any

from PIL import Image


def image_bytes(image_format: str = "PNG", size: tuple[int, int] = (32, 32), mode: str = "RGB") -> bytes:
    buffer = BytesIO()
    color: Any = (200, 80, 40, 128) if mode == "RGBA" else (200, 80, 40)
    Image.new(mode, size, color).save(buffer, format=image_format)
    return buffer.getvalue()


OUTPUT_PNG = image_bytes("PNG")
OUTPUT_JPEG = image_bytes("JPEG")
DRAFT_PNG = image_bytes("PNG", (8, 8))
# The start of an MP4 file. The engine reads these bytes to check the file extension.
OUTPUT_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isommp42" + b"\x00" * 64
CHAT_REPLY = "Hello from the fake APIYI"
VIDEO_TASK_ID = "video_test"
SEEDANCE_TASK_ID = "cgt-test"
SEEDANCE_TASKS_PATH = "/seedance/api/v3/contents/generations/tasks"
VIDEO_FILE_PATH = "/files/out.mp4"
TASK_READ = re.compile(rf"(/v1/videos|{SEEDANCE_TASKS_PATH})/([^/]+)")


@dataclass
class UploadedFile:
    filename: str
    content_type: str
    content: bytes


@dataclass
class RecordedRequest:
    method: str
    path: str
    # An email.message.Message: lookups ignore case, the same as HTTP header names.
    headers: Any
    json: dict[str, Any] | None = None
    form: dict[str, str] = field(default_factory=dict)
    files: list[tuple[str, UploadedFile]] = field(default_factory=list)

    def files_named(self, name: str) -> list[UploadedFile]:
        return [file for field_name, file in self.files if field_name == name]


class FakeApiyi:
    """Answers like APIYI.

    Queue an answer with `answer_next` to replace the default answer of a POST, and with `answer_next_read`
    to replace the default answer of a GET.
    """

    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        self._queued: deque[tuple[int, Any]] = deque()
        self._queued_reads: deque[tuple[int, Any]] = deque()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                """Keep the test output clean."""

            def do_GET(self) -> None:  # noqa: N802
                fake.requests.append(RecordedRequest("GET", self.path, self.headers))
                if fake._queued_reads:
                    status, answer = fake._queued_reads.popleft()
                    content = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
                    self._reply(status, content, "application/json")
                elif self.path.endswith("/content") or self.path == VIDEO_FILE_PATH:
                    self._reply(200, OUTPUT_MP4, "video/mp4")
                elif match := TASK_READ.fullmatch(self.path):
                    self._reply(200, json.dumps(fake._finished_task(*match.groups())).encode(), "application/json")
                else:
                    self._reply(200, OUTPUT_JPEG, "image/jpeg")

            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                fake.requests.append(_recorded(self.path, self.headers, body))
                if self.path == "/v1/chat/completions":
                    self._reply(200, _chat_stream(fake.requests[-1].json["model"]), "text/event-stream")
                    return
                status, answer = fake._answer(self.path, fake.requests[-1])
                self._reply(status, json.dumps(answer).encode(), "application/json")

            def _reply(self, status: int, content: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def answer_next(self, status: int, body: Any) -> None:
        self._queued.append((status, body))

    def answer_next_read(self, status: int, body: Any) -> None:
        self._queued_reads.append((status, body))

    @property
    def posts(self) -> list[RecordedRequest]:
        return [request for request in self.requests if request.method == "POST"]

    @property
    def last_post(self) -> RecordedRequest:
        return self.posts[-1]

    @property
    def reads(self) -> list[RecordedRequest]:
        return [request for request in self.requests if request.method == "GET"]

    def _answer(self, path: str, request: RecordedRequest) -> tuple[int, Any]:
        if self._queued:
            return self._queued.popleft()
        if ":generateContent" in path:
            return 200, _gemini_answer()
        if path == "/v1/videos":
            return 200, {"id": VIDEO_TASK_ID, "status": "queued"}
        if path == SEEDANCE_TASKS_PATH:
            return 200, {"id": SEEDANCE_TASK_ID}
        model = (request.json or request.form).get("model", "")
        if model.startswith("flux"):
            return 200, {"created": 1, "data": [{"url": f"{self.url}/files/out.jpeg?signature=abc"}]}
        count = ((request.json or {}).get("sequential_image_generation_options") or {}).get("max_images", 1)
        return 200, {"created": 1, "data": [{"b64_json": _b64(OUTPUT_PNG)} for _ in range(count)]}

    def _finished_task(self, tasks_path: str, task_id: str) -> dict[str, Any]:
        link = f"{self.url}{VIDEO_FILE_PATH}"
        if tasks_path == SEEDANCE_TASKS_PATH:
            return {"id": task_id, "status": "succeeded", "content": {"video_url": link}}
        return {"id": task_id, "status": "completed", "progress": 100, "video_url": link}


def _chat_stream(model: str) -> bytes:
    """An OpenAI-style stream that sends the reply in two pieces."""
    first, second = CHAT_REPLY[:5], CHAT_REPLY[5:]
    deltas = [({"role": "assistant", "content": first}, None), ({"content": second}, None), ({}, "stop")]
    events = [
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        for delta, finish_reason in deltas
    ]
    lines = [f"data: {json.dumps(event)}\n\n" for event in events]
    return "".join([*lines, "data: [DONE]\n\n"]).encode()


def _gemini_answer() -> dict[str, Any]:
    """A text part, a draft, then the final image, because the parts have no fixed order."""
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Here is the image."},
                        {"inlineData": {"mimeType": "image/png", "data": _b64(DRAFT_PNG)}},
                        {"inlineData": {"mimeType": "image/png", "data": _b64(OUTPUT_PNG)}},
                    ]
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 258},
    }


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def _recorded(path: str, headers: Any, body: bytes) -> RecordedRequest:
    content_type = headers.get("Content-Type", "")
    if content_type.startswith("multipart/form-data"):
        form, files = _parse_multipart(content_type, body)
        return RecordedRequest("POST", path, headers, form=form, files=files)
    return RecordedRequest("POST", path, headers, json=json.loads(body))


def _parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], list[tuple[str, UploadedFile]]]:
    message = BytesParser().parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    form: dict[str, str] = {}
    files: list[tuple[str, UploadedFile]] = []
    for part in message.get_payload():
        name = part.get_param("name", header="content-disposition")
        content = part.get_payload(decode=True)
        filename = part.get_filename()
        if filename:
            files.append((name, UploadedFile(filename, part.get_content_type(), content)))
        else:
            form[name] = content.decode()
    return form, files
