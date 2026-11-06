"""The APIYI Prompt node builds the config that the standard Agent node reads."""

from __future__ import annotations

from pathlib import Path

import pytest
from griptape_nodes.exe_types.core_types import NodeError
from griptape_nodes.retained_mode.events.execution_events import ExecuteNodeResultSuccess

from .conftest import create_node, run_node
from .fake_apiyi import FakeApiyi


async def test_the_config_has_the_wire_form_of_the_standard_model_config(fake: FakeApiyi) -> None:
    result = await run_node(create_node("ApiyiPrompt", model="gpt-6-luna"))

    assert isinstance(result, ExecuteNodeResultSuccess), result.result_details
    assert result.parameter_output_values["prompt_model_config"] == {
        "provider": "openai_compatible",
        "model": "gpt-6-luna",
        "base_url": f"{fake.url}/v1",
        "api_key_secret": "APIYI_API_KEY",
        "settings": {},
        "max_retries": 1,
    }


async def test_the_config_holds_the_name_of_the_secret_and_never_its_value() -> None:
    result = await run_node(create_node("ApiyiPrompt"))

    assert "sk-test-key" not in str(result.parameter_output_values["prompt_model_config"])


async def test_no_temperature_is_sent_by_default_because_newer_models_refuse_it() -> None:
    result = await run_node(create_node("ApiyiPrompt"))

    assert result.parameter_output_values["prompt_model_config"]["settings"] == {}


async def test_set_values_reach_the_settings() -> None:
    result = await run_node(create_node("ApiyiPrompt", temperature=0.0, max_tokens=500, max_attempts_on_fail=3))

    config = result.parameter_output_values["prompt_model_config"]
    assert config["settings"] == {"temperature": 0.0, "max_tokens": 500}
    assert config["max_retries"] == 2


async def test_a_typed_model_id_is_kept() -> None:
    result = await run_node(create_node("ApiyiPrompt", model="a-model-from-next-week"))

    assert result.parameter_output_values["prompt_model_config"]["model"] == "a-model-from-next-week"


async def test_a_missing_key_stops_the_node(no_api_key: None) -> None:
    node = create_node("ApiyiPrompt")

    problems = node.validate_before_workflow_run()

    assert problems is not None
    assert isinstance(problems[0], NodeError)
    assert "APIYI_API_KEY is not set" in str(problems[0])


async def test_a_v1_suffix_in_the_base_url_secret_is_not_doubled(
    tmp_path: Path, fake: FakeApiyi, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(f"APIYI_API_KEY=key\nAPIYI_BASE_URL={fake.url}/v1/\n")
    monkeypatch.setenv("APIYI_BASE_URL", f"{fake.url}/v1/")

    result = await run_node(create_node("ApiyiPrompt"))

    assert result.parameter_output_values["prompt_model_config"]["base_url"] == f"{fake.url}/v1"
