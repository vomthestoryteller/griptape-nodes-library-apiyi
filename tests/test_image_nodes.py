"""Each test runs a real node against the fake server, then checks what reached the server.

The expected requests come from the APIYI documentation. Many of the checks guard a documented trap:
a field that causes a 400 on one model line, or that bills more on another.
"""

from __future__ import annotations

import base64
import json
from io import BytesIO
from os import urandom
from pathlib import Path
from typing import Any

import httpx
import pytest
from griptape.artifacts import ImageArtifact
from griptape.artifacts.image_url_artifact import ImageUrlArtifact
from griptape_nodes.exe_types.core_types import NodeError
from griptape_nodes.exe_types.node_types import BaseNode
from griptape_nodes.files.file import File
from griptape_nodes.retained_mode.events.execution_events import ExecuteNodeResultSuccess
from griptape_nodes.traits.options import Options
from PIL import Image

from griptape_nodes_library_apiyi.apiyi_images import COMPRESS_ABOVE_BYTES, MAX_LONG_EDGE

from .conftest import API_KEY, create_node, run_node
from .fake_apiyi import OUTPUT_JPEG, OUTPUT_PNG, FakeApiyi, image_bytes

IMAGE_NODES = ["ApiyiGptImage", "ApiyiNanoBanana", "ApiyiFlux", "ApiyiSeedream", "ApiyiMaiImage"]
PROMPT = "a red fox in the snow"
# Wider than the compression limit of 2048 pixels, and large in bytes when the pixels are noise.
LARGE_SIZE = (2200, 1000)


def choices(node: BaseNode, name: str) -> list[str]:
    return node.get_parameter_by_name(name).find_elements_by_type(Options)[0].choices


def hidden(node: BaseNode, name: str) -> bool:
    return bool(node.get_parameter_by_name(name).ui_options.get("hide"))


def local_image(tmp_path: Path, name: str = "input.png", content: bytes | None = None) -> str:
    path = tmp_path / name
    path.write_bytes(content or image_bytes())
    return str(path)


def as_data_uri(content: bytes, mime_type: str = "image/png") -> str:
    return f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"


async def generate(node: BaseNode) -> ExecuteNodeResultSuccess:
    result = await run_node(node)
    assert isinstance(result, ExecuteNodeResultSuccess), result.result_details
    assert result.parameter_output_values["was_successful"] is True
    return result


async def saved_bytes(artifact: ImageUrlArtifact) -> bytes:
    return await File(artifact.value).aread_bytes()


# ---- Shared behaviour ----


@pytest.mark.parametrize("node_type", IMAGE_NODES)
async def test_a_generated_image_is_saved_and_returned(node_type: str, fake: FakeApiyi) -> None:
    result = await generate(create_node(node_type, prompt=PROMPT))

    artifact = result.parameter_output_values["image"]
    assert isinstance(artifact, ImageUrlArtifact)
    assert await saved_bytes(artifact) in (OUTPUT_PNG, OUTPUT_JPEG)
    assert fake.last_post.headers["Authorization"] == f"Bearer {API_KEY}"


@pytest.mark.parametrize("node_type", IMAGE_NODES)
async def test_an_empty_prompt_stops_the_node_before_any_request(node_type: str, fake: FakeApiyi) -> None:
    result = await run_node(create_node(node_type, prompt="   "))

    assert not result.succeeded()
    assert "'Prompt' is empty" in str(result.result_details)
    assert fake.posts == []


async def test_a_missing_key_is_reported_alone_with_a_link_to_settings(no_api_key: None, fake: FakeApiyi) -> None:
    node = create_node("ApiyiGptImage", prompt="")

    problems = node.validate_before_node_run()

    assert problems is not None
    assert len(problems) == 1
    assert isinstance(problems[0], NodeError)
    assert "APIYI_API_KEY is not set" in str(problems[0])
    assert problems[0].links[0].url.startswith("#settings-secrets")
    assert fake.posts == []


async def test_base64_with_a_data_prefix_decodes(fake: FakeApiyi) -> None:
    fake.answer_next(200, {"data": [{"b64_json": as_data_uri(OUTPUT_PNG)}]})

    result = await generate(create_node("ApiyiGptImage", prompt=PROMPT))

    assert await saved_bytes(result.parameter_output_values["image"]) == OUTPUT_PNG


async def test_provider_response_keeps_no_image_data(fake: FakeApiyi) -> None:
    large_png = image_bytes("BMP", (64, 64))
    fake.answer_next(200, {"data": [{"b64_json": base64.b64encode(large_png).decode("ascii")}]})

    result = await generate(create_node("ApiyiGptImage", prompt=PROMPT))

    assert "characters removed" in result.parameter_output_values["provider_response"]["data"][0]["b64_json"]


async def test_an_error_inside_a_200_answer_is_shown(fake: FakeApiyi) -> None:
    fake.answer_next(200, {"error": {"message": "the upstream refused this request"}})

    result = await run_node(create_node("ApiyiGptImage", prompt=PROMPT))

    assert "the upstream refused this request" in str(result.result_details)


async def test_rate_limits_are_retried_because_they_are_not_billed(fake: FakeApiyi) -> None:
    fake.answer_next(429, {"error": {"message": "too many requests"}})
    fake.answer_next(503, {"error": {"message": "overloaded"}})

    await generate(create_node("ApiyiGptImage", prompt=PROMPT))

    assert len(fake.posts) == 3


async def test_retries_stop_and_report_the_last_answer(fake: FakeApiyi) -> None:
    for _ in range(4):
        fake.answer_next(429, {"error": {"message": "too many requests"}})

    result = await run_node(create_node("ApiyiGptImage", prompt=PROMPT))

    assert not result.succeeded()
    assert "too many requests" in str(result.result_details)
    assert len(fake.posts) == 4


async def test_a_server_error_is_retried_because_it_is_not_billed(fake: FakeApiyi) -> None:
    fake.answer_next(500, {"error": {"message": "Internal server error"}})
    fake.answer_next(500, {"error": {"message": "Internal server error"}})

    await generate(create_node("ApiyiFlux", prompt=PROMPT))

    assert len(fake.posts) == 3


async def test_a_server_error_gets_two_retries_at_most(fake: FakeApiyi) -> None:
    for _ in range(4):
        fake.answer_next(500, {"error": {"message": "Internal server error (request id: abc)"}})

    result = await run_node(create_node("ApiyiFlux", prompt=PROMPT))

    assert not result.succeeded()
    assert "request id: abc" in str(result.result_details)
    assert len(fake.posts) == 3


@pytest.mark.parametrize("status", [502, 504])
async def test_a_gateway_error_is_not_retried_because_the_model_can_still_run(status: int, fake: FakeApiyi) -> None:
    fake.answer_next(status, {"error": {"message": "upstream timed out"}})

    result = await run_node(create_node("ApiyiGptImage", prompt=PROMPT))

    assert not result.succeeded()
    assert len(fake.posts) == 1


async def test_a_400_is_not_retried_and_shows_the_provider_message(fake: FakeApiyi) -> None:
    fake.answer_next(400, {"error": {"message": "size is not valid", "code": "invalid_value"}})

    result = await run_node(create_node("ApiyiGptImage", prompt=PROMPT))

    assert not result.succeeded()
    assert "size is not valid" in str(result.result_details)
    assert len(fake.posts) == 1


async def test_a_403_is_not_reported_as_a_bad_key(fake: FakeApiyi) -> None:
    fake.answer_next(403, {"error": {"message": "content review blocked this prompt"}})

    result = await run_node(create_node("ApiyiFlux", prompt=PROMPT))

    assert "content review blocked this prompt" in str(result.result_details)
    assert "refused the API key" not in str(result.result_details)


async def test_a_401_points_at_the_key(fake: FakeApiyi) -> None:
    fake.answer_next(401, {"error": {"message": "invalid token"}})

    result = await run_node(create_node("ApiyiFlux", prompt=PROMPT))

    assert "refused the API key" in str(result.result_details)


async def test_a_failure_follows_the_failed_output_when_it_is_wired(fake: FakeApiyi) -> None:
    fake.answer_next(400, {"error": {"message": "bad request"}})
    node = create_node("ApiyiGptImage", prompt=PROMPT)
    node._has_outgoing_connections = lambda _parameter: True

    await node.aprocess()

    assert node.parameter_output_values["was_successful"] is False
    assert "bad request" in node.parameter_output_values["result_details"]
    assert node.parameter_output_values["image"] is None


async def test_a_timeout_says_the_request_can_still_be_billed(fake: FakeApiyi, monkeypatch: pytest.MonkeyPatch) -> None:
    def time_out(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr("httpx.AsyncClient.post", time_out)

    result = await run_node(create_node("ApiyiGptImage", prompt=PROMPT))

    assert "can still bill" in str(result.result_details)


@pytest.mark.parametrize(
    "value",
    [
        ImageArtifact(image_bytes(), format="png", width=32, height=32),
        as_data_uri(image_bytes()),
    ],
    ids=["ImageArtifact", "data-uri"],
)
async def test_input_images_accept_each_kind_of_value(value: Any, fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiGptImage", prompt=PROMPT, input_images=[value]))

    assert fake.last_post.files_named("image[]")[0].content == image_bytes()


async def test_a_local_path_is_read_as_an_input_image(tmp_path: Path, fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiGptImage", prompt=PROMPT, input_images=[local_image(tmp_path)]))

    assert fake.last_post.files_named("image[]")[0].content == image_bytes()


async def test_a_missing_input_file_gives_a_clear_error(tmp_path: Path, fake: FakeApiyi) -> None:
    missing = ImageUrlArtifact(str(tmp_path / "missing.png"))

    result = await run_node(create_node("ApiyiGptImage", prompt=PROMPT, input_images=[missing]))

    assert "Could not read 'Input Images' item 1" in str(result.result_details)
    assert fake.posts == []


async def test_a_large_input_image_is_compressed(tmp_path: Path, fake: FakeApiyi) -> None:
    large = _noise_png(*LARGE_SIZE)
    assert len(large) > COMPRESS_ABOVE_BYTES

    await generate(create_node("ApiyiGptImage", prompt=PROMPT, input_images=[local_image(tmp_path, content=large)]))

    upload = fake.last_post.files_named("image[]")[0]
    assert upload.content_type == "image/jpeg"
    assert len(upload.content) < len(large)
    assert max(_size_of(upload.content)) == MAX_LONG_EDGE


async def test_compression_can_be_turned_off(tmp_path: Path, fake: FakeApiyi) -> None:
    large = _noise_png(*LARGE_SIZE)

    await generate(
        create_node(
            "ApiyiGptImage",
            prompt=PROMPT,
            input_images=[local_image(tmp_path, content=large)],
            compress_input_images=False,
        )
    )

    assert fake.last_post.files_named("image[]")[0].content == large


async def test_a_phone_mpo_file_is_encoded_again_as_jpeg(tmp_path: Path, fake: FakeApiyi) -> None:
    mpo = image_bytes("MPO")

    await generate(create_node("ApiyiGptImage", prompt=PROMPT, input_images=[local_image(tmp_path, "p.jpg", mpo)]))

    upload = fake.last_post.files_named("image[]")[0]
    assert upload.content_type == "image/jpeg"
    assert _format_of(upload.content) == "JPEG"


# ---- GPT Image ----


async def test_official_generation_sends_every_documented_field_and_no_forbidden_one(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiGptImage", prompt=PROMPT, size="2048x1152", quality="high"))

    request = fake.last_post
    assert request.path == "/v1/images/generations"
    assert request.json == {
        "model": "gpt-image-2.5-flare",
        "prompt": PROMPT,
        "size": "2048x1152",
        "quality": "high",
        "output_format": "png",
        "background": "auto",
        "moderation": "auto",
    }


async def test_compression_is_sent_only_for_formats_that_use_it(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiGptImage", prompt=PROMPT, output_format="jpeg", output_compression=70))

    assert fake.last_post.json["output_compression"] == 70


async def test_official_edit_uses_repeated_image_fields(tmp_path: Path, fake: FakeApiyi) -> None:
    images = [local_image(tmp_path, "a.png"), local_image(tmp_path, "b.png")]

    await generate(create_node("ApiyiGptImage", prompt=PROMPT, input_images=images, quality="high"))

    request = fake.last_post
    assert request.path == "/v1/images/edits"
    assert len(request.files_named("image[]")) == 2
    assert request.form["quality"] == "high"
    assert "moderation" not in request.form
    assert request.headers["Content-Type"].startswith("multipart/form-data; boundary=")


async def test_a_mask_is_uploaded_and_turns_compression_off(tmp_path: Path, fake: FakeApiyi) -> None:
    large = _noise_png(*LARGE_SIZE)
    mask = image_bytes("PNG", LARGE_SIZE, "RGBA")

    await generate(
        create_node(
            "ApiyiGptImage",
            prompt=PROMPT,
            input_images=[local_image(tmp_path, "a.png", large)],
            mask=local_image(tmp_path, "mask.png", mask),
        )
    )

    request = fake.last_post
    assert request.files_named("mask")[0].content == mask
    assert request.files_named("image[]")[0].content == large


async def test_a_mask_without_alpha_is_refused(tmp_path: Path, fake: FakeApiyi) -> None:
    result = await run_node(
        create_node(
            "ApiyiGptImage",
            prompt=PROMPT,
            input_images=[local_image(tmp_path)],
            mask=local_image(tmp_path, "mask.png", image_bytes("PNG", mode="RGB")),
        )
    )

    assert "no alpha channel" in str(result.result_details)
    assert fake.posts == []


async def test_a_mask_needs_an_input_image(tmp_path: Path, fake: FakeApiyi) -> None:
    mask = local_image(tmp_path, "mask.png", image_bytes("PNG", mode="RGBA"))

    result = await run_node(create_node("ApiyiGptImage", prompt=PROMPT, mask=mask))

    assert "Connect an image to 'Input Images'" in str(result.result_details)
    assert fake.posts == []


async def test_transparent_with_jpeg_is_refused_before_a_request(fake: FakeApiyi) -> None:
    node = create_node("ApiyiGptImage", prompt=PROMPT, background="transparent", output_format="jpeg")

    result = await run_node(node)

    assert "needs PNG or WEBP" in str(result.result_details)
    assert fake.posts == []


@pytest.mark.parametrize("model", ["gpt-image-2.5-vip", "gpt-image-2-vip"])
async def test_vip_sends_size_and_quality_and_never_n(model: str, fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiGptImage", prompt=PROMPT, model=model, size="2048x1360", quality="high"))

    assert fake.last_post.json == {
        "model": model,
        "prompt": PROMPT,
        "size": "2048x1360",
        "quality": "high",
        "response_format": "b64_json",
    }


async def test_vip_edit_uses_the_plain_image_field(tmp_path: Path, fake: FakeApiyi) -> None:
    node = create_node("ApiyiGptImage", prompt=PROMPT, model="gpt-image-2.5-vip", input_images=[local_image(tmp_path)])

    await generate(node)

    assert len(fake.last_post.files_named("image")) == 1
    assert fake.last_post.files_named("image[]") == []
    assert fake.last_post.form["response_format"] == "b64_json"


async def test_all_sends_no_size_or_quality(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiGptImage", prompt=PROMPT, model="gpt-image-2.5-all"))

    assert fake.last_post.json == {"model": "gpt-image-2.5-all", "prompt": PROMPT, "response_format": "b64_json"}


async def test_complexity_is_sent_only_for_2_6_models(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiGptImage", prompt=PROMPT, model="gpt-image-2.6-flare", complexity="low"))
    assert fake.last_post.json["complexity"] == "low"

    await generate(create_node("ApiyiGptImage", prompt=PROMPT, model="gpt-image-2.5-flare", complexity="low"))
    assert "complexity" not in fake.last_post.json


def test_model_change_matches_the_gpt_parameters() -> None:
    node = create_node("ApiyiGptImage")

    node.set_parameter_value("model", "gpt-image-2")
    assert "xhigh" not in choices(node, "quality")
    assert "transparent" not in choices(node, "background")

    node.set_parameter_value("model", "gpt-image-2.5-vip")
    assert "3840x1632" in choices(node, "size")
    assert "xhigh" in choices(node, "quality")
    assert hidden(node, "mask")
    assert hidden(node, "output_format")

    node.set_parameter_value("model", "gpt-image-2.5-all")
    assert hidden(node, "size")
    assert hidden(node, "quality")

    node.set_parameter_value("model", "gpt-image-2.6-sunburst")
    assert not hidden(node, "size")
    assert not hidden(node, "mask")
    assert not hidden(node, "complexity")


def test_a_quality_the_new_model_refuses_moves_to_a_valid_one() -> None:
    node = create_node("ApiyiGptImage", model="gpt-image-2.5-flare", quality="max")

    node.set_parameter_value("model", "gpt-image-2")

    assert node.get_parameter_value("quality") == "high"


def test_a_typed_size_survives_a_model_change() -> None:
    node = create_node("ApiyiGptImage", size="1600x1600")

    node.set_parameter_value("model", "gpt-image-2.5-sunburst")

    assert node.get_parameter_value("size") == "1600x1600"


def test_jpeg_shows_the_compression_setting() -> None:
    node = create_node("ApiyiGptImage")
    assert hidden(node, "output_compression")

    node.set_parameter_value("output_format", "jpeg")

    assert not hidden(node, "output_compression")


# ---- Nano Banana ----


async def test_nano_banana_uses_the_gemini_native_request(fake: FakeApiyi) -> None:
    await generate(
        create_node("ApiyiNanoBanana", prompt=PROMPT, model="gemini-3-pro-image", aspect_ratio="16:9", image_size="2K")
    )

    request = fake.last_post
    assert request.path == "/v1beta/models/gemini-3-pro-image:generateContent"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.json == {
        "contents": [{"parts": [{"text": PROMPT}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "16:9", "imageSize": "2K"},
        },
    }


async def test_nano_banana_takes_the_last_image_because_earlier_ones_can_be_drafts() -> None:
    result = await generate(create_node("ApiyiNanoBanana", prompt=PROMPT))

    assert await saved_bytes(result.parameter_output_values["image"]) == OUTPUT_PNG


async def test_nano_banana_input_images_go_in_their_own_parts(tmp_path: Path, fake: FakeApiyi) -> None:
    images = [local_image(tmp_path, "a.png"), local_image(tmp_path, "b.png")]

    await generate(create_node("ApiyiNanoBanana", prompt=PROMPT, input_images=images))

    parts = fake.last_post.json["contents"][0]["parts"]
    assert parts[0] == {"text": PROMPT}
    assert [set(part) for part in parts[1:]] == [{"inlineData"}, {"inlineData"}]
    assert parts[1]["inlineData"]["mimeType"] == "image/png"
    assert base64.b64decode(parts[1]["inlineData"]["data"]) == image_bytes()


async def test_nano_banana_converts_webp_because_gemini_takes_only_png_and_jpeg(
    tmp_path: Path, fake: FakeApiyi
) -> None:
    webp = local_image(tmp_path, "a.webp", image_bytes("WEBP"))

    await generate(create_node("ApiyiNanoBanana", prompt=PROMPT, input_images=[webp]))

    assert fake.last_post.json["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "image/jpeg"


async def test_auto_aspect_ratio_sends_no_ratio(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiNanoBanana", prompt=PROMPT, aspect_ratio="auto"))

    assert fake.last_post.json["generationConfig"]["imageConfig"] == {"imageSize": "1K"}


async def test_a_large_gemini_signature_does_not_reach_the_workflow(fake: FakeApiyi) -> None:
    image = {"mimeType": "image/png", "data": base64.b64encode(OUTPUT_PNG).decode("ascii")}
    part = {"thoughtSignature": "A" * 2_500_000, "inlineData": image}
    fake.answer_next(200, {"candidates": [{"content": {"parts": [part]}, "finishReason": "STOP"}]})

    result = await generate(create_node("ApiyiNanoBanana", prompt=PROMPT))

    assert await saved_bytes(result.parameter_output_values["image"]) == OUTPUT_PNG
    assert len(json.dumps(result.parameter_output_values["provider_response"])) < 2000


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"candidates": [{"finishReason": "IMAGE_SAFETY", "content": {}}]}, "IMAGE_SAFETY"),
        ({"promptFeedback": {"blockReason": "OTHER"}}, "OTHER"),
        (
            {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "I can't make that."}]}}]},
            "I can't make that.",
        ),
    ],
    ids=["safety-stop", "blocked-input", "text-refusal"],
)
async def test_http_200_without_an_image_explains_why(answer: dict[str, Any], expected: str, fake: FakeApiyi) -> None:
    fake.answer_next(200, answer)

    result = await run_node(create_node("ApiyiNanoBanana", prompt=PROMPT))

    assert not result.succeeded()
    assert expected in str(result.result_details)


def test_model_change_matches_the_nano_banana_parameters() -> None:
    node = create_node("ApiyiNanoBanana", model="gemini-3.1-flash-image")
    assert "512" in choices(node, "image_size")
    assert "8:1" in choices(node, "aspect_ratio")
    node.set_parameter_value("image_size", "512")
    node.set_parameter_value("aspect_ratio", "8:1")

    node.set_parameter_value("model", "gemini-3-pro-image")

    assert "512" not in choices(node, "image_size")
    assert "8:1" not in choices(node, "aspect_ratio")
    assert node.get_parameter_value("image_size") == "1K"
    assert node.get_parameter_value("aspect_ratio") == "1:1"


# ---- Flux ----


async def test_flux_generation_request(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiFlux", prompt=PROMPT, size="1536x1024", seed=7))

    request = fake.posts[0]
    assert request.path == "/v1/images/generations"
    assert request.json == {
        "model": "flux-2-pro",
        "prompt": PROMPT,
        "seed": 7,
        "output_format": "jpeg",
        "safety_tolerance": 2,
        "size": "1536x1024",
    }


async def test_flux_downloads_the_image_at_once_and_does_not_send_the_key_to_the_vendor(fake: FakeApiyi) -> None:
    result = await generate(create_node("ApiyiFlux", prompt=PROMPT))

    download = next(request for request in fake.requests if request.method == "GET")
    assert download.path.startswith("/files/out.jpeg")
    assert "Authorization" not in download.headers
    assert await saved_bytes(result.parameter_output_values["image"]) == OUTPUT_JPEG


async def test_a_failed_download_keeps_the_link_for_the_user(fake: FakeApiyi) -> None:
    fake.answer_next(200, {"data": [{"url": "http://127.0.0.1:1/gone.jpeg"}]})

    result = await run_node(create_node("ApiyiFlux", prompt=PROMPT))

    assert "the download failed" in str(result.result_details)


async def test_flux_edit_numbers_the_input_images_and_sends_no_size(tmp_path: Path, fake: FakeApiyi) -> None:
    images = [local_image(tmp_path, "a.png"), "https://example.com/b.png", local_image(tmp_path, "c.png")]

    await generate(create_node("ApiyiFlux", prompt=PROMPT, input_images=images, aspect_ratio="16:9"))

    body = fake.posts[0].json
    assert body["input_image"].startswith("data:image/png;base64,")
    assert body["input_image_2"] == "https://example.com/b.png"
    assert body["input_image_3"].startswith("data:image/png;base64,")
    assert body["aspect_ratio"] == "16:9"
    assert "size" not in body


@pytest.mark.parametrize(("model", "limit"), [("flux-kontext-pro", 1), ("flux-2-klein-4b", 4)])
async def test_flux_input_limits_depend_on_the_model(model: str, limit: int, fake: FakeApiyi) -> None:
    images = [f"https://example.com/{index}.png" for index in range(limit + 1)]

    result = await run_node(create_node("ApiyiFlux", prompt=PROMPT, model=model, input_images=images))

    assert f"accepts {limit}" in str(result.result_details)
    assert fake.posts == []


async def test_only_flex_sends_steps_and_guidance(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiFlux", prompt=PROMPT, model="flux-2-flex", steps=30, guidance=6.0))
    assert fake.posts[-1].json["steps"] == 30
    assert fake.posts[-1].json["guidance"] == 6.0

    await generate(create_node("ApiyiFlux", prompt=PROMPT, model="flux-2-max"))
    assert "steps" not in fake.posts[-1].json


async def test_klein_never_sends_prompt_upsampling(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiFlux", prompt=PROMPT, model="flux-2-klein-4b", prompt_upsampling=True))

    assert "prompt_upsampling" not in fake.posts[-1].json


async def test_randomize_seed_changes_the_seed(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiFlux", prompt=PROMPT, seed=7, randomize_seed=True))

    assert fake.posts[-1].json["seed"] != 7


# ---- Seedream ----


async def test_seedream_generation_request(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiSeedream", prompt=PROMPT, size="3K", output_format="png"))

    request = fake.last_post
    assert request.path == "/v1/images/generations"
    assert request.json == {
        "model": "seedream-5-0-260128",
        "prompt": PROMPT,
        "size": "3K",
        "response_format": "b64_json",
        "watermark": False,
        "output_format": "png",
        "sequential_image_generation": "disabled",
    }


@pytest.mark.parametrize("model", ["seedream-5-0-pro-260628", "seedream-5-0-flash-260915"])
async def test_pro_and_flash_never_get_the_sequence_field_because_it_causes_a_400(model: str, fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiSeedream", prompt=PROMPT, model=model, max_images=4))

    assert "sequential_image_generation" not in fake.last_post.json
    assert "sequential_image_generation_options" not in fake.last_post.json


async def test_jpeg_only_models_get_no_output_format(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiSeedream", prompt=PROMPT, model="seedream-4-5-251128"))

    assert "output_format" not in fake.last_post.json


async def test_a_sequence_saves_each_image_under_its_own_name(fake: FakeApiyi) -> None:
    result = await generate(create_node("ApiyiSeedream", prompt=PROMPT, max_images=3))

    assert fake.last_post.json["sequential_image_generation"] == "auto"
    assert fake.last_post.json["sequential_image_generation_options"] == {"max_images": 3}
    artifacts = result.parameter_output_values["all_images"]
    assert len({artifact.value for artifact in artifacts}) == 3
    assert result.parameter_output_values["image"] == artifacts[0]


async def test_seedream_input_images_are_an_array(tmp_path: Path, fake: FakeApiyi) -> None:
    images = ["https://example.com/a.png", local_image(tmp_path)]

    await generate(create_node("ApiyiSeedream", prompt=PROMPT, input_images=images))

    assert fake.last_post.json["image"][0] == "https://example.com/a.png"
    assert fake.last_post.json["image"][1].startswith("data:image/png;base64,")


async def test_input_plus_output_images_cannot_pass_the_total_limit(fake: FakeApiyi) -> None:
    images = [f"https://example.com/{index}.png" for index in range(10)]

    result = await run_node(create_node("ApiyiSeedream", prompt=PROMPT, input_images=images, max_images=6))

    assert "the limit is 15" in str(result.result_details)
    assert fake.posts == []


def test_a_preset_of_another_model_moves_to_a_valid_one() -> None:
    node = create_node("ApiyiSeedream", size="3K")

    node.set_parameter_value("model", "seedream-5-0-flash-260915")

    assert node.get_parameter_value("size") == "1K"


def test_model_change_matches_the_seedream_parameters() -> None:
    node = create_node("ApiyiSeedream")

    node.set_parameter_value("model", "seedream-4-5-251128")
    assert hidden(node, "output_format")
    assert "4K" in choices(node, "size")

    node.set_parameter_value("model", "seedream-5-0-flash-260915")
    assert hidden(node, "max_images")
    assert "1.5K" in choices(node, "size")


# ---- MAI-Image ----


async def test_mai_sends_width_and_height_and_no_forbidden_field(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiMaiImage", prompt=PROMPT, size="1536x1024"))

    assert fake.last_post.path == "/v1/images/generations"
    assert fake.last_post.json == {"model": "MAI-Image-2.6", "prompt": PROMPT, "width": 1536, "height": 1024}


async def test_mai_edit_sends_form_fields_and_repeated_images(tmp_path: Path, fake: FakeApiyi) -> None:
    images = [local_image(tmp_path, "a.png"), local_image(tmp_path, "b.png")]

    await generate(create_node("ApiyiMaiImage", prompt=PROMPT, size="1536x1024", input_images=images))

    request = fake.last_post
    assert request.path == "/v1/images/edits"
    assert request.form == {"model": "MAI-Image-2.6", "prompt": PROMPT, "width": "1536", "height": "1024"}
    assert len(request.files_named("image[]")) == 2


@pytest.mark.parametrize(
    ("size", "expected"), [("big", "must look like"), ("512x512", "768"), ("1600x1600", "2,359,296")]
)
async def test_mai_refuses_a_size_the_api_would_refuse(size: str, expected: str, fake: FakeApiyi) -> None:
    result = await run_node(create_node("ApiyiMaiImage", prompt=PROMPT, size=size))

    assert expected in str(result.result_details)
    assert fake.posts == []


def test_every_model_list_has_no_duplicates() -> None:
    for node_type in IMAGE_NODES:
        node = create_node(node_type)
        models = choices(node, "model")
        assert len(models) == len(set(models))
        assert node.get_parameter_value("model") in models


def _noise_png(width: int, height: int) -> bytes:
    """A PNG that does not compress, so it stays above the compression limit."""
    buffer = BytesIO()
    Image.frombytes("RGB", (width, height), urandom(3 * width * height)).save(buffer, format="PNG")
    return buffer.getvalue()


def _size_of(content: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(content)) as image:
        return image.size


def _format_of(content: bytes) -> str | None:
    with Image.open(BytesIO(content)) as image:
        return image.format


def test_the_manifest_lists_every_node_file() -> None:
    manifest = json.loads((Path(__file__).parent.parent / "griptape-nodes-library.json").read_text())

    for node in manifest["nodes"]:
        assert (Path(__file__).parent.parent / node["file_path"]).is_file()
