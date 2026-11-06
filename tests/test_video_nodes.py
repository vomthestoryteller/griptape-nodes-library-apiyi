"""Each video node, against a fake APIYI. These tests read the real requests that the nodes send."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from griptape.artifacts.video_url_artifact import VideoUrlArtifact
from griptape_nodes.exe_types.node_types import BaseNode
from griptape_nodes.files.file import File
from griptape_nodes.retained_mode.engine import current_engine
from griptape_nodes.retained_mode.events.execution_events import ExecuteNodeResultSuccess
from griptape_nodes.retained_mode.events.workflow_events import (
    RunWorkflowFromScratchRequest,
    RunWorkflowFromScratchResultSuccess,
    SaveWorkflowRequest,
    SaveWorkflowResultSuccess,
)
from griptape_nodes.traits.options import Options

from .conftest import API_KEY, create_node, run_node
from .fake_apiyi import (
    OUTPUT_MP4,
    SEEDANCE_TASK_ID,
    SEEDANCE_TASKS_PATH,
    VIDEO_FILE_PATH,
    VIDEO_TASK_ID,
    FakeApiyi,
    image_bytes,
)

VIDEO_NODES = ["ApiyiOxygen", "ApiyiVeo", "ApiyiGrokVideo", "ApiyiSeedance"]
NODES_WITH_GROUP_KEYS = {"ApiyiGrokVideo": "APIYI_GROK_API_KEY", "ApiyiSeedance": "APIYI_SEEDANCE_API_KEY"}
PROMPT = "a paper boat on a calm lake"
PUBLIC_IMAGE = "https://example.com/frame.png"
PUBLIC_VIDEO = "https://example.com/clip.mp4"
PUBLIC_AUDIO = "https://example.com/track.mp3"
MODEL_25 = "doubao-seedance-2-5-260628"
MODEL_20 = "doubao-seedance-2-0-260128"
QUOTA_ERROR = "user [1] quota [8732] preConsumedQuota [150000] is not enough"
NO_CHANNEL_ERROR = "该模型无可用渠道"
REFRESH_BUTTON = "generation_refresh"


def choices(node: BaseNode, name: str) -> list[str]:
    return node.get_parameter_by_name(name).find_elements_by_type(Options)[0].choices


def hidden(node: BaseNode, name: str) -> bool:
    return bool(node.get_parameter_by_name(name).ui_options.get("hide"))


def local_image(tmp_path: Path) -> str:
    path = tmp_path / "frame.png"
    path.write_bytes(image_bytes())
    return str(path)


def click_refresh(node: BaseNode) -> None:
    node.get_parameter_by_name(REFRESH_BUTTON).on_click_callback(None, None)


def envelope_of(fake: FakeApiyi) -> dict[str, Any]:
    return json.loads(fake.last_post.json["input_reference"])


async def generate(node: BaseNode) -> ExecuteNodeResultSuccess:
    result = await run_node(node)
    assert isinstance(result, ExecuteNodeResultSuccess), result.result_details
    assert result.parameter_output_values["was_successful"] is True
    return result


async def fail(node: BaseNode) -> str:
    """Run a node that must fail, and return the details it shows."""
    result = await run_node(node)
    assert not result.succeeded()
    return str(node.parameter_output_values.get("result_details") or result.result_details)


# ---- Shared behaviour ----


@pytest.mark.parametrize("node_type", VIDEO_NODES)
async def test_a_video_is_saved_and_returned(node_type: str, fake: FakeApiyi) -> None:
    result = await generate(create_node(node_type, prompt=PROMPT))

    artifact = result.parameter_output_values["video"]
    assert isinstance(artifact, VideoUrlArtifact)
    assert artifact.name.endswith(".mp4")
    assert await File(artifact.value).aread_bytes() == OUTPUT_MP4
    assert result.parameter_output_values["generation_id"] in (VIDEO_TASK_ID, SEEDANCE_TASK_ID)
    assert "Saved as" in result.parameter_output_values["result_details"]
    assert fake.last_post.headers["Authorization"] == f"Bearer {API_KEY}"


@pytest.mark.parametrize("node_type", VIDEO_NODES)
async def test_an_empty_prompt_stops_the_node_before_any_request(node_type: str, fake: FakeApiyi) -> None:
    result = await run_node(create_node(node_type, prompt="  "))

    assert not result.succeeded()
    assert "Describe the video you want" in str(result.result_details)
    assert fake.requests == []


@pytest.mark.parametrize("node_type", VIDEO_NODES)
async def test_a_missing_key_is_reported_before_any_request(no_api_key: None, node_type: str, fake: FakeApiyi) -> None:
    node = create_node(node_type, prompt=PROMPT)

    problems = node.validate_before_node_run()

    assert problems is not None
    assert "APIYI_API_KEY is not set" in str(problems[0])
    assert fake.requests == []


@pytest.mark.parametrize("node_type", VIDEO_NODES)
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_a_refused_submit_is_not_sent_again_because_a_person_decides(
    node_type: str, status: int, fake: FakeApiyi
) -> None:
    fake.answer_next(status, {"error": {"message": "try later"}})

    details = await fail(create_node(node_type, prompt=PROMPT))

    assert "try later" in details
    assert len(fake.posts) == 1
    assert fake.reads == []


@pytest.mark.parametrize("node_type", VIDEO_NODES)
async def test_a_quota_error_shows_the_apiyi_message(node_type: str, fake: FakeApiyi) -> None:
    fake.answer_next(403, {"error": {"message": QUOTA_ERROR, "type": "new_api_error"}})

    details = await fail(create_node(node_type, prompt=PROMPT))

    assert QUOTA_ERROR in details
    assert "refused the API key" not in details
    assert len(fake.posts) == 1


async def test_an_error_inside_a_json_string_shows_the_real_reason(fake: FakeApiyi) -> None:
    nested = {"error": {"code": "invalid_params", "message": "seconds must be 4 to 15", "param": "input.duration"}}
    fake.answer_next(400, {"message": json.dumps(nested), "type": "task_error", "code": "fail_to_fetch_task"})

    details = await fail(create_node("ApiyiOxygen", prompt=PROMPT))

    assert details == "seconds must be 4 to 15"


async def test_an_answer_with_no_task_id_warns_about_the_bill(fake: FakeApiyi) -> None:
    fake.answer_next(200, {"status": "queued"})

    details = await fail(create_node("ApiyiOxygen", prompt=PROMPT))

    assert "no task ID" in details
    assert "can bill" in details


async def test_an_error_inside_a_200_answer_is_shown(fake: FakeApiyi) -> None:
    fake.answer_next(200, {"error": {"message": QUOTA_ERROR}})

    details = await fail(create_node("ApiyiOxygen", prompt=PROMPT))

    assert details == QUOTA_ERROR


async def test_a_failed_task_shows_the_reason_and_downloads_nothing(fake: FakeApiyi) -> None:
    fake.answer_next_read(
        200, {"id": VIDEO_TASK_ID, "status": "failed", "error": {"code": "x", "message": "no capacity"}}
    )

    node = create_node("ApiyiOxygen", prompt=PROMPT)
    details = await fail(node)

    assert details == "The video failed: no capacity"
    assert len(fake.reads) == 1
    assert node.parameter_output_values["generation_id"] == VIDEO_TASK_ID


async def test_a_task_failure_needs_no_reason_in_the_answer(fake: FakeApiyi) -> None:
    fake.answer_next_read(200, {"id": VIDEO_TASK_ID, "status": "failed"})

    details = await fail(create_node("ApiyiOxygen", prompt=PROMPT))

    assert "ended with the status failed" in details


async def test_the_state_of_the_task_shows_the_progress(fake: FakeApiyi) -> None:
    fake.answer_next_read(200, {"id": VIDEO_TASK_ID, "status": "in_progress", "progress": 40})

    node = create_node("ApiyiOxygen", prompt=PROMPT)
    await generate(node)

    assert len(fake.reads) == 3  # in progress, completed, and the download
    assert node.parameter_output_values["generation_status"] == "completed (100%)"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_a_brief_read_failure_does_not_stop_a_paid_task(status: int, fake: FakeApiyi) -> None:
    fake.answer_next_read(status, {"error": {"message": "busy"}})

    await generate(create_node("ApiyiOxygen", prompt=PROMPT))

    assert len(fake.posts) == 1


async def test_an_unreadable_task_answer_does_not_stop_a_paid_task(fake: FakeApiyi) -> None:
    fake.answer_next_read(200, b'id":"cut off')

    await generate(create_node("ApiyiSeedance", prompt=PROMPT))

    assert len(fake.posts) == 1


async def test_an_unreadable_submit_answer_warns_about_the_bill(fake: FakeApiyi) -> None:
    fake.answer_next(200, "not an object")

    details = await fail(create_node("ApiyiSeedance", prompt=PROMPT))

    assert "APIYI can bill this request" in details
    assert "Run the node again" not in details


@pytest.mark.parametrize("status", [401, 404])
async def test_a_refused_read_stops_the_node_and_keeps_the_task(status: int, fake: FakeApiyi) -> None:
    fake.answer_next_read(status, {"error": {"message": "not allowed"}})

    node = create_node("ApiyiOxygen", prompt=PROMPT)
    await fail(node)

    assert node.parameter_output_values["generation_id"] == VIDEO_TASK_ID
    assert len(fake.reads) == 1


async def test_the_failed_output_receives_a_task_failure(fake: FakeApiyi) -> None:
    fake.answer_next_read(200, {"id": VIDEO_TASK_ID, "status": "failed", "error": {"message": "no capacity"}})

    node = create_node("ApiyiOxygen", prompt=PROMPT)
    await fail(node)

    assert node.parameter_output_values["was_successful"] is False


# ---- Stop waiting, then collect the video ----


async def _run_until_timeout(fake: FakeApiyi) -> BaseNode:
    """Run a node whose task takes longer than the shortest timeout. Oxygen reads the task each 5 seconds."""
    for _ in range(12):
        fake.answer_next_read(200, {"id": VIDEO_TASK_ID, "status": "in_progress", "progress": 10})
    node = create_node("ApiyiOxygen", prompt=PROMPT, timeout=60)
    await fail(node)
    return node


async def test_a_timeout_keeps_the_task_and_warns_about_a_new_run(fake: FakeApiyi, pauses: list[int]) -> None:
    node = await _run_until_timeout(fake)

    details = node.parameter_output_values["result_details"]
    assert "did not finish in 60 seconds" in details
    assert "Do not run the node again" in details
    assert node.parameter_output_values["generation_id"] == VIDEO_TASK_ID
    assert sum(pauses) == 60
    assert len(fake.posts) == 1


async def test_the_refresh_button_collects_a_video_after_a_timeout(fake: FakeApiyi) -> None:
    node = await _run_until_timeout(fake)

    click_refresh(node)

    assert node.parameter_output_values["was_successful"] is True
    assert isinstance(node.parameter_output_values["video"], VideoUrlArtifact)
    assert "is complete" in node.parameter_output_values["result_details"]
    assert len(fake.posts) == 1


async def test_the_refresh_button_starts_no_new_task_while_the_task_runs(fake: FakeApiyi) -> None:
    node = await _run_until_timeout(fake)
    fake.answer_next_read(200, {"id": VIDEO_TASK_ID, "status": "queued"})

    click_refresh(node)

    assert node.parameter_output_values["was_successful"] is False
    assert "has not finished" in node.parameter_output_values["result_details"]
    assert node.parameter_output_values.get("video") is None
    assert len(fake.posts) == 1


async def test_the_refresh_button_shows_a_failed_task(fake: FakeApiyi) -> None:
    node = await _run_until_timeout(fake)
    fake.answer_next_read(200, {"id": VIDEO_TASK_ID, "status": "failed", "error": {"message": "no capacity"}})

    click_refresh(node)

    assert node.parameter_output_values["was_successful"] is False
    assert "no capacity" in node.parameter_output_values["result_details"]


async def test_the_refresh_button_shows_a_read_failure(fake: FakeApiyi) -> None:
    node = await _run_until_timeout(fake)
    fake.answer_next_read(503, {"error": {"message": "busy"}})

    click_refresh(node)

    assert "busy" in node.parameter_output_values["result_details"]


async def test_the_refresh_button_needs_a_task(fake: FakeApiyi) -> None:
    node = create_node("ApiyiOxygen", prompt=PROMPT)

    click_refresh(node)

    assert "no task" in node.parameter_output_values["result_details"]
    assert fake.requests == []


async def test_a_reopened_workflow_can_still_collect_the_video(fake: FakeApiyi, tmp_path: Path) -> None:
    node = await _run_until_timeout(fake)
    engine = current_engine()

    saved = await engine.ahandle_request(SaveWorkflowRequest(file_name="apiyi_video_saved"))
    assert isinstance(saved, SaveWorkflowResultSuccess), saved.result_details
    workflow_file = next((tmp_path / "workspace").rglob("apiyi_video_saved.py"))
    assert API_KEY not in workflow_file.read_text(encoding="utf-8")

    loaded = await engine.ahandle_request(RunWorkflowFromScratchRequest(file_path=str(workflow_file)))
    assert isinstance(loaded, RunWorkflowFromScratchResultSuccess), loaded.result_details
    reopened = next(
        candidate
        for candidate in engine.object_manager.get_filtered_subset(type=BaseNode).values()
        if type(candidate).__name__ == "ApiyiOxygen"
    )
    assert reopened is not node

    click_refresh(reopened)

    assert isinstance(reopened.parameter_output_values.get("video"), VideoUrlArtifact), reopened.parameter_output_values
    assert len(fake.posts) == 1


async def test_a_cancel_request_stops_the_wait_and_keeps_the_task(fake: FakeApiyi) -> None:
    fake.answer_next_read(200, {"id": VIDEO_TASK_ID, "status": "in_progress"})
    node = create_node("ApiyiOxygen", prompt=PROMPT)
    node.request_cancellation()

    details = await fail(node)

    assert "APIYI has no way to stop a task" in details
    assert node.parameter_output_values["generation_id"] == VIDEO_TASK_ID
    assert len(fake.reads) == 1


async def test_a_new_run_clears_the_last_task(fake: FakeApiyi) -> None:
    node = await _run_until_timeout(fake)
    fake.answer_next(403, {"error": {"message": QUOTA_ERROR}})

    await fail(node)

    assert not node.parameter_output_values["generation_id"]


async def test_a_download_failure_keeps_the_task_and_a_refresh_collects_the_video(
    fake: FakeApiyi, pauses: list[int]
) -> None:
    fake.answer_next_read(
        200, {"id": VIDEO_TASK_ID, "status": "completed", "video_url": f"{fake.url}{VIDEO_FILE_PATH}"}
    )
    for _ in range(8):
        fake.answer_next_read(503, {"error": {"message": "storage is busy"}})

    node = create_node("ApiyiOxygen", prompt=PROMPT)
    details = await fail(node)

    assert "the download failed: storage is busy" in details
    assert "Do not run the node again" in details
    assert pauses.count(4) == 7

    click_refresh(node)

    assert node.parameter_output_values["was_successful"] is True


# ---- Keys and token groups ----


@pytest.mark.parametrize(("node_type", "secret"), NODES_WITH_GROUP_KEYS.items())
async def test_a_group_key_wins_over_the_main_key(
    node_type: str, secret: str, tmp_path: Path, fake: FakeApiyi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(secret, "sk-group-key")

    await generate(create_node(node_type, prompt=PROMPT))

    assert {request.headers["Authorization"] for request in fake.posts} == {"Bearer sk-group-key"}
    task_reads = [request for request in fake.reads if not request.path.endswith((".mp4", "/content"))]
    assert {request.headers["Authorization"] for request in task_reads} == {"Bearer sk-group-key"}


async def test_a_refused_group_key_names_the_group_key(fake: FakeApiyi, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIYI_SEEDANCE_API_KEY", "sk-group-key")
    fake.answer_next(401, {"error": {"message": "invalid token"}})

    details = await fail(create_node("ApiyiSeedance", prompt=PROMPT))

    assert "APIYI_SEEDANCE_API_KEY" in details


@pytest.mark.parametrize(
    ("node_type", "status", "body", "expected"),
    [
        ("ApiyiGrokVideo", 503, {"error": {"message": NO_CHANNEL_ERROR}}, "GrokOfficial"),
        ("ApiyiSeedance", 500, {"error": {"message": NO_CHANNEL_ERROR}}, "SeeDance2"),
        ("ApiyiOxygen", 503, {"error": {"message": "unavailable"}}, "default or svip"),
        ("ApiyiVeo", 503, {"error": {"message": "unavailable"}}, "按次计费"),
    ],
)
async def test_a_token_without_the_right_group_gets_the_steps_to_fix_it(
    node_type: str, status: int, body: dict[str, Any], expected: str, fake: FakeApiyi
) -> None:
    fake.answer_next(status, body)

    details = await fail(create_node(node_type, prompt=PROMPT))

    assert expected in details


# ---- Oxygen ----


async def test_oxygen_sends_only_the_five_top_level_fields(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiOxygen", prompt=PROMPT, seconds=4, resolution="480p", aspect_ratio="9:16"))

    assert fake.last_post.path == "/v1/videos"
    assert fake.last_post.json == {"model": "oxygen-1.0", "prompt": PROMPT, "seconds": "4", "size": "720x1280"}


async def test_oxygen_downloads_the_link_without_the_key(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiOxygen", prompt=PROMPT))

    download = fake.reads[-1]
    assert download.path == VIDEO_FILE_PATH
    assert download.headers["Authorization"] is None


async def test_oxygen_uses_the_content_path_with_the_key_if_a_task_has_no_link(fake: FakeApiyi) -> None:
    fake.answer_next_read(200, {"id": VIDEO_TASK_ID, "status": "completed"})

    await generate(create_node("ApiyiOxygen", prompt=PROMPT))

    assert fake.reads[-1].path == f"/v1/videos/{VIDEO_TASK_ID}/content"
    assert fake.reads[-1].headers["Authorization"] == f"Bearer {API_KEY}"


async def test_oxygen_puts_frames_in_an_envelope_string(fake: FakeApiyi) -> None:
    node = create_node("ApiyiOxygen", prompt=PROMPT, first_frame=PUBLIC_IMAGE, last_frame=PUBLIC_IMAGE)

    await generate(node)

    assert isinstance(fake.last_post.json["input_reference"], str)
    assert envelope_of(fake) == {"images": [PUBLIC_IMAGE], "last_image": PUBLIC_IMAGE}


async def test_oxygen_sends_a_local_frame_as_a_data_uri(tmp_path: Path, fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiOxygen", prompt=PROMPT, first_frame=local_image(tmp_path)))

    uri = envelope_of(fake)["images"][0]
    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == image_bytes()


async def test_oxygen_puts_a_square_shape_in_the_envelope_and_keeps_size(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiOxygen", prompt=PROMPT, resolution="768p", aspect_ratio="1:1"))

    assert fake.last_post.json["size"] == "1792x1024"
    assert envelope_of(fake) == {"resolution": "768p", "aspect_ratio": "1:1"}


async def test_oxygen_sends_no_shape_with_a_first_frame_because_the_frame_sets_it(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiOxygen", prompt=PROMPT, aspect_ratio="1:1", first_frame=PUBLIC_IMAGE))

    assert envelope_of(fake) == {"images": [PUBLIC_IMAGE]}


async def test_oxygen_sends_settings_that_differ_from_the_defaults(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiOxygen", prompt=PROMPT, quality="normal", prompt_enhance="none"))

    assert envelope_of(fake) == {"quality": "normal", "prompt_enhance": "none"}


async def test_oxygen_sends_references(fake: FakeApiyi) -> None:
    node = create_node(
        "ApiyiOxygen",
        prompt=PROMPT,
        reference_images=[PUBLIC_IMAGE],
        reference_videos=[PUBLIC_VIDEO],
        reference_audio=[PUBLIC_AUDIO],
    )

    await generate(node)

    assert envelope_of(fake) == {
        "reference_images": [PUBLIC_IMAGE],
        "reference_videos": [PUBLIC_VIDEO],
        "reference_audios": [PUBLIC_AUDIO],
    }


async def test_oxygen_refuses_frames_with_references(fake: FakeApiyi) -> None:
    node = create_node("ApiyiOxygen", prompt=PROMPT, first_frame=PUBLIC_IMAGE, reference_videos=[PUBLIC_VIDEO])

    result = await run_node(node)

    assert "not both" in str(result.result_details)
    assert fake.posts == []


async def test_a_last_frame_needs_a_first_frame(fake: FakeApiyi) -> None:
    result = await run_node(create_node("ApiyiOxygen", prompt=PROMPT, last_frame=PUBLIC_IMAGE))

    assert "needs 'First Frame'" in str(result.result_details)
    assert fake.posts == []


async def test_oxygen_refuses_too_many_references(fake: FakeApiyi) -> None:
    node = create_node("ApiyiOxygen", prompt=PROMPT, reference_videos=[PUBLIC_VIDEO] * 3)
    node.get_parameter_list_value = lambda name: [PUBLIC_VIDEO] * 4 if name == "reference_videos" else []  # type: ignore[method-assign]

    assert "4 items are in 'Reference Videos', and oxygen-1.0 accepts 3" in str(node.validate_before_node_run())


async def test_a_reference_video_needs_a_public_link(tmp_path: Path, fake: FakeApiyi) -> None:
    details = await fail(create_node("ApiyiOxygen", prompt=PROMPT, reference_videos=[str(tmp_path / "clip.mp4")]))

    assert "'Reference Videos' item 1 is not a public link" in details
    assert fake.posts == []


# ---- Veo ----


async def test_veo_text_request_has_the_settings_in_metadata(fake: FakeApiyi) -> None:
    node = create_node(
        "ApiyiVeo",
        prompt=PROMPT,
        resolution="1080p",
        aspect_ratio="9:16",
        negative_prompt="blurry",
        randomize_seed=False,
        seed=7,
    )

    await generate(node)

    assert fake.last_post.json == {
        "model": "veo-3.1-fast-generate-preview",
        "prompt": PROMPT,
        "seconds": "8",
        "size": "1080x1920",
        "metadata": {"resolution": "1080p", "aspectRatio": "9:16", "seed": 7, "negativePrompt": "blurry"},
    }


async def test_veo_image_request_is_multipart_with_plain_fields(tmp_path: Path, fake: FakeApiyi) -> None:
    node = create_node("ApiyiVeo", prompt=PROMPT, first_frame=local_image(tmp_path), randomize_seed=False, seed=7)

    await generate(node)

    assert fake.last_post.form == {
        "model": "veo-3.1-fast-generate-preview",
        "prompt": PROMPT,
        "seconds": "8",
        "size": "1280x720",
        "resolution": "720p",
        "aspectRatio": "16:9",
        "seed": "7",
    }
    uploaded = fake.last_post.files_named("input_reference")
    assert len(uploaded) == 1
    assert uploaded[0].content == image_bytes()


async def test_veo_reads_a_task_id_from_either_field(fake: FakeApiyi) -> None:
    fake.answer_next(200, {"task_id": "veo_task", "status": "queued"})

    await generate(create_node("ApiyiVeo", prompt=PROMPT))

    assert fake.reads[0].path == "/v1/videos/veo_task"


async def test_veo_downloads_the_content_path_with_the_key(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiVeo", prompt=PROMPT))

    assert fake.reads[-1].path == f"/v1/videos/{VIDEO_TASK_ID}/content"
    assert fake.reads[-1].headers["Authorization"] == f"Bearer {API_KEY}"


async def test_veo_waits_when_the_file_is_not_ready_just_after_the_task_completes(
    fake: FakeApiyi, pauses: list[int]
) -> None:
    fake.answer_next_read(200, {"id": VIDEO_TASK_ID, "status": "completed"})
    fake.answer_next_read(400, {"error": {"message": "task status is IN_PROGRESS"}})

    await generate(create_node("ApiyiVeo", prompt=PROMPT))

    assert pauses[-1] == 4


async def test_veo_offers_only_8_seconds_at_high_resolutions(fake: FakeApiyi) -> None:
    node = create_node("ApiyiVeo", prompt=PROMPT, seconds="4")

    node.set_parameter_value("resolution", "4k")
    assert choices(node, "seconds") == ["8"]
    assert node.get_parameter_value("seconds") == "8"

    node.set_parameter_value("resolution", "720p")
    assert choices(node, "seconds") == ["8", "6", "4"]


async def test_veo_refuses_a_connected_length_that_the_resolution_cannot_use(fake: FakeApiyi) -> None:
    node = create_node("ApiyiVeo", prompt=PROMPT, resolution="1080p")
    node.parameter_values["seconds"] = "4"

    result = await run_node(node)

    assert "needs a length of 8 seconds" in str(result.result_details)
    assert fake.posts == []


# ---- Grok Imagine Video ----


async def test_grok_sends_length_and_size(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiGrokVideo", prompt=PROMPT, seconds=3, size="720x1280"))

    assert fake.last_post.path == "/v1/videos"
    assert fake.last_post.json == {
        "model": "grok-imagine-video-1.5-lite",
        "prompt": PROMPT,
        "seconds": "3",
        "size": "720x1280",
    }


async def test_grok_sends_a_local_frame_as_a_data_uri(tmp_path: Path, fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiGrokVideo", prompt=PROMPT, first_frame=local_image(tmp_path)))

    assert fake.last_post.json["input_reference"].startswith("data:image/png;base64,")


async def test_grok_sends_a_public_frame_as_a_link(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiGrokVideo", prompt=PROMPT, first_frame=PUBLIC_IMAGE))

    assert fake.last_post.json["input_reference"] == PUBLIC_IMAGE


async def test_grok_downloads_the_content_path_with_the_key(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiGrokVideo", prompt=PROMPT))

    assert fake.reads[-1].path == f"/v1/videos/{VIDEO_TASK_ID}/content"
    assert fake.reads[-1].headers["Authorization"] == f"Bearer {API_KEY}"


async def test_grok_says_that_a_blocked_video_was_billed(fake: FakeApiyi) -> None:
    fake.answer_next_read(
        200, {"id": VIDEO_TASK_ID, "status": "completed", "error": {"code": "moderation_blocked", "message": "blocked"}}
    )

    details = await fail(create_node("ApiyiGrokVideo", prompt=PROMPT))

    assert "APIYI billed the task" in details
    assert not any(request.path.endswith("/content") for request in fake.reads)


async def test_the_basic_grok_model_has_no_1080p(fake: FakeApiyi) -> None:
    node = create_node("ApiyiGrokVideo", prompt=PROMPT, size="1920x1080")

    node.set_parameter_value("model", "grok-imagine-video")

    assert "1920x1080" not in choices(node, "size")[:4]
    result = await run_node(node)
    assert "no 1080p output" in str(result.result_details)
    assert fake.posts == []


# ---- Seedance ----


async def test_seedance_sends_its_own_body_to_its_own_endpoint(fake: FakeApiyi) -> None:
    node = create_node(
        "ApiyiSeedance",
        prompt=PROMPT,
        resolution="480p",
        ratio="16:9",
        duration=6,
        generate_audio=False,
        watermark=True,
        randomize_seed=False,
        seed=7,
    )

    await generate(node)

    assert fake.last_post.path == SEEDANCE_TASKS_PATH
    assert fake.last_post.json == {
        "model": "doubao-seedance-2-0-mini-260615",
        "content": [{"type": "text", "text": PROMPT}],
        "resolution": "480p",
        "ratio": "16:9",
        "duration": 6,
        "generate_audio": False,
        "watermark": True,
        "seed": 7,
    }


async def test_seedance_asks_for_a_plain_answer_on_each_gateway_request(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiSeedance", prompt=PROMPT))

    assert fake.last_post.headers["Accept-Encoding"] == "identity"
    assert fake.reads[0].path == f"{SEEDANCE_TASKS_PATH}/{SEEDANCE_TASK_ID}"
    assert fake.reads[0].headers["Accept-Encoding"] == "identity"


async def test_seedance_waits_as_the_documents_advise(fake: FakeApiyi, pauses: list[int]) -> None:
    fake.answer_next_read(200, {"id": SEEDANCE_TASK_ID, "status": "running"})

    await generate(create_node("ApiyiSeedance", prompt=PROMPT))

    assert pauses == [25, 15]


async def test_seedance_downloads_the_link_without_the_key(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiSeedance", prompt=PROMPT))

    assert fake.reads[-1].path == VIDEO_FILE_PATH
    assert fake.reads[-1].headers["Authorization"] is None


@pytest.mark.parametrize("status", ["failed", "expired"])
async def test_seedance_stops_on_each_failed_state(status: str, fake: FakeApiyi) -> None:
    fake.answer_next_read(200, {"id": SEEDANCE_TASK_ID, "status": status, "error": {"message": "stopped"}})

    details = await fail(create_node("ApiyiSeedance", prompt=PROMPT))

    assert "stopped" in details


async def test_seedance_needs_a_link_in_a_task_that_succeeded(fake: FakeApiyi) -> None:
    fake.answer_next_read(200, {"id": SEEDANCE_TASK_ID, "status": "succeeded", "content": {}})

    details = await fail(create_node("ApiyiSeedance", prompt=PROMPT))

    assert "no link to the video" in details


async def test_seedance_marks_the_frames_with_their_roles(fake: FakeApiyi) -> None:
    node = create_node("ApiyiSeedance", prompt=PROMPT, first_frame=PUBLIC_IMAGE, last_frame=PUBLIC_IMAGE)

    await generate(node)

    assert fake.last_post.json["content"][1:] == [
        {"type": "image_url", "image_url": {"url": PUBLIC_IMAGE}, "role": "first_frame"},
        {"type": "image_url", "image_url": {"url": PUBLIC_IMAGE}, "role": "last_frame"},
    ]


async def test_seedance_marks_the_references_with_their_roles(fake: FakeApiyi) -> None:
    node = create_node(
        "ApiyiSeedance",
        prompt=PROMPT,
        reference_images=[PUBLIC_IMAGE],
        reference_videos=[PUBLIC_VIDEO],
        reference_audio=[PUBLIC_AUDIO],
    )

    await generate(node)

    assert fake.last_post.json["content"][1:] == [
        {"type": "image_url", "image_url": {"url": PUBLIC_IMAGE}, "role": "reference_image"},
        {"type": "video_url", "video_url": {"url": PUBLIC_VIDEO}, "role": "reference_video"},
        {"type": "audio_url", "audio_url": {"url": PUBLIC_AUDIO}, "role": "reference_audio"},
    ]


async def test_seedance_refuses_frames_with_references(fake: FakeApiyi) -> None:
    node = create_node("ApiyiSeedance", prompt=PROMPT, first_frame=PUBLIC_IMAGE, reference_images=[PUBLIC_IMAGE])

    result = await run_node(node)

    assert "not both" in str(result.result_details)
    assert fake.posts == []


async def test_seedance_limits_the_references_by_model(fake: FakeApiyi) -> None:
    node = create_node("ApiyiSeedance", prompt=PROMPT, model=MODEL_20, reference_images=[PUBLIC_IMAGE] * 10)

    result = await run_node(node)

    assert f"10 items are in 'Reference Images', and {MODEL_20} accepts 9" in str(result.result_details)

    node.set_parameter_value("model", MODEL_25)

    await generate(node)


async def test_seedance_2_0_needs_an_image_or_video_with_audio(fake: FakeApiyi) -> None:
    node = create_node("ApiyiSeedance", prompt=PROMPT, model=MODEL_20, reference_audio=[PUBLIC_AUDIO])

    result = await run_node(node)

    assert "needs a reference image or video" in str(result.result_details)

    node.set_parameter_value("model", MODEL_25)

    await generate(node)


@pytest.mark.parametrize(
    ("model", "duration", "allowed"), [(MODEL_20, 16, False), (MODEL_25, 30, True), (MODEL_25, 31, False)]
)
async def test_seedance_limits_the_length_by_model(model: str, duration: int, allowed: bool, fake: FakeApiyi) -> None:
    node = create_node("ApiyiSeedance", prompt=PROMPT, model=model, duration=duration)

    if allowed:
        await generate(node)
    else:
        result = await run_node(node)
        assert "'Duration' is" in str(result.result_details)
        assert fake.posts == []


async def test_seedance_accepts_a_length_that_the_model_chooses(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiSeedance", prompt=PROMPT, duration=-1))

    assert fake.last_post.json["duration"] == -1


async def test_seedance_offers_1080p_only_on_models_that_have_it(fake: FakeApiyi) -> None:
    node = create_node("ApiyiSeedance", prompt=PROMPT, model=MODEL_25, resolution="1080p")
    assert choices(node, "resolution") == ["1080p", "720p", "480p"]

    node.set_parameter_value("model", "doubao-seedance-2-0-fast-260128")

    assert choices(node, "resolution") == ["720p", "480p"]
    assert node.get_parameter_value("resolution") == "720p"


async def test_seedance_shows_the_newest_settings_only_on_2_5(fake: FakeApiyi) -> None:
    node = create_node("ApiyiSeedance", prompt=PROMPT)
    assert hidden(node, "output_format")
    assert hidden(node, "reference_task")

    node.set_parameter_value("model", MODEL_25)

    assert not hidden(node, "output_format")
    assert not hidden(node, "reference_task")


async def test_seedance_2_5_sends_its_own_settings(fake: FakeApiyi) -> None:
    node = create_node(
        "ApiyiSeedance",
        prompt=PROMPT,
        model=MODEL_25,
        reference_videos=[PUBLIC_VIDEO],
        reference_task="extend",
        output_format="mov",
    )

    await generate(node)

    assert fake.last_post.json["output_format"] == "mov"
    assert fake.last_post.json["omni_reference_task_type"] == "extend"


async def test_seedance_2_0_never_sends_the_settings_of_2_5(fake: FakeApiyi) -> None:
    node = create_node("ApiyiSeedance", prompt=PROMPT, model=MODEL_25, output_format="mov", reference_task="extend")
    node.set_parameter_value("model", MODEL_20)

    await generate(node)

    assert "output_format" not in fake.last_post.json
    assert "omni_reference_task_type" not in fake.last_post.json


async def test_seedance_2_5_needs_the_adaptive_ratio_with_a_frame(fake: FakeApiyi) -> None:
    node = create_node("ApiyiSeedance", prompt=PROMPT, model=MODEL_25, ratio="16:9", first_frame=PUBLIC_IMAGE)

    result = await run_node(node)

    assert "needs the ratio 'adaptive'" in str(result.result_details)
    assert fake.posts == []


async def test_seedance_2_0_accepts_any_ratio_with_a_frame(fake: FakeApiyi) -> None:
    await generate(create_node("ApiyiSeedance", prompt=PROMPT, model=MODEL_20, ratio="16:9", first_frame=PUBLIC_IMAGE))


@pytest.mark.parametrize(
    ("task", "values", "expected"),
    [
        ("extend", {}, "needs a video"),
        ("edit", {"reference_videos": [PUBLIC_VIDEO]}, "needs the duration -1"),
        ("edit", {"reference_videos": [PUBLIC_VIDEO], "duration": -1, "ratio": "16:9"}, "needs the ratio 'adaptive'"),
    ],
)
async def test_seedance_2_5_checks_edit_and_extend_tasks(
    task: str, values: dict[str, Any], expected: str, fake: FakeApiyi
) -> None:
    node = create_node("ApiyiSeedance", prompt=PROMPT, model=MODEL_25, reference_task=task, **values)

    result = await run_node(node)

    assert expected in str(result.result_details)
    assert fake.posts == []


# ---- Values that the engine restores ----


async def test_a_saved_model_shapes_the_dropdowns_during_a_load(fake: FakeApiyi) -> None:
    node = create_node("ApiyiGrokVideo")

    node.set_parameter_value("model", "grok-imagine-video", initial_setup=True)

    assert "1920x1080" not in choices(node, "size")


async def test_a_saved_resolution_shapes_the_lengths_during_a_load(fake: FakeApiyi) -> None:
    node = create_node("ApiyiVeo")

    node.set_parameter_value("resolution", "4k", initial_setup=True)

    assert choices(node, "seconds") == ["8"]
