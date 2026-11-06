"""Run APIYI Prompt -> Agent -> APIYI GPT Image in a real flow, with the real standard library.

The standard library is large, and its Python packages live in its own `.venv`, so these tests are
off by default. To turn them on, set GTN_STANDARD_LIBRARY to the folder of the standard library.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from griptape_nodes.exe_types.core_types import ParameterMode
from griptape_nodes.exe_types.node_types import BaseNode
from griptape_nodes.retained_mode.engine import current_engine
from griptape_nodes.retained_mode.events.connection_events import CreateConnectionRequest
from griptape_nodes.retained_mode.events.execution_events import (
    ResolveNodeRequest,
    ResolveNodeResultSuccess,
    StartFlowRequest,
    StartFlowResultSuccess,
)
from griptape_nodes.retained_mode.events.library_events import (
    RegisterLibraryFromFileRequest,
    RegisterLibraryFromFileResultSuccess,
)
from griptape_nodes.retained_mode.events.node_events import (
    CreateNodeRequest,
    CreateNodeResultSuccess,
    DeserializeSelectedNodesFromCommandsRequest,
    DeserializeSelectedNodesFromCommandsResultSuccess,
    SerializeSelectedNodesToCommandsRequest,
    SerializeSelectedNodesToCommandsResultSuccess,
)
from griptape_nodes.retained_mode.events.parameter_events import SetParameterValueRequest
from griptape_nodes.retained_mode.events.workflow_events import (
    RunWorkflowFromScratchRequest,
    RunWorkflowFromScratchResultSuccess,
    SaveWorkflowRequest,
    SaveWorkflowResultSuccess,
)

from .conftest import API_KEY, FLOW_NAME, create_node
from .fake_apiyi import CHAT_REPLY, FakeApiyi

STANDARD_LIBRARY = os.environ.get("GTN_STANDARD_LIBRARY")
STANDARD_LIBRARY_NAME = "Griptape Nodes Library"

pytestmark = pytest.mark.skipif(not STANDARD_LIBRARY, reason="Set GTN_STANDARD_LIBRARY to run these tests.")


@pytest.fixture(autouse=True)
def standard_library(engine: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Register a copy of the standard library, with its packages taken from its existing `.venv`."""
    source = Path(STANDARD_LIBRARY or "")
    # Appended, so that the engine in this environment wins over a copy of the engine in the `.venv`.
    monkeypatch.setattr(sys, "path", [*sys.path, str(source / ".venv" / "Lib" / "site-packages")])

    target = tmp_path / "standard"
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".venv", ".git", "__pycache__", "tests"))
    manifest_path = target / "griptape_nodes_library.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["metadata"]["dependencies"]["pip_dependencies"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = current_engine().handle_request(RegisterLibraryFromFileRequest(file_path=str(manifest_path)))
    assert isinstance(result, RegisterLibraryFromFileResultSuccess), result.result_details


def _agent() -> BaseNode:
    engine = current_engine()
    result = engine.handle_request(
        CreateNodeRequest(
            node_type="Agent", specific_library_name=STANDARD_LIBRARY_NAME, override_parent_flow_name=FLOW_NAME
        )
    )
    assert isinstance(result, CreateNodeResultSuccess), result.result_details
    return engine.node_manager.get_node_by_name(result.node_name)


def _connect(source: BaseNode, source_parameter: str, target: BaseNode, target_parameter: str) -> None:
    result = current_engine().handle_request(
        CreateConnectionRequest(
            source_node_name=source.name,
            source_parameter_name=source_parameter,
            target_node_name=target.name,
            target_parameter_name=target_parameter,
        )
    )
    assert result.succeeded(), result.result_details


def _set(node: BaseNode, name: str, value: Any) -> None:
    result = current_engine().handle_request(
        SetParameterValueRequest(node_name=node.name, parameter_name=name, value=value)
    )
    assert result.succeeded(), result.result_details


async def _run_flow() -> None:
    result = await current_engine().ahandle_request(StartFlowRequest(flow_name=FLOW_NAME))
    assert isinstance(result, StartFlowResultSuccess), result.result_details


def test_the_agent_accepts_the_prompt_node() -> None:
    prompt_node = create_node("ApiyiPrompt")
    agent = _agent()

    _connect(prompt_node, "prompt_model_config", agent, "model")

    model = agent.get_parameter_by_name("model")
    assert model.allowed_modes == {ParameterMode.INPUT}
    assert model.ui_options["display_name"] == "prompt model config"


async def test_an_agent_chats_through_apiyi_without_a_griptape_cloud_credential(fake: FakeApiyi) -> None:
    prompt_node = create_node("ApiyiPrompt", model="gpt-6-luna")
    agent = _agent()
    _connect(prompt_node, "prompt_model_config", agent, "model")
    _set(agent, "prompt", "Say hello")

    await _run_flow()

    assert agent.get_parameter_value("output") == CHAT_REPLY
    request = fake.last_post
    assert request.path == "/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.json["model"] == "gpt-6-luna"
    assert request.json["stream"] is True
    assert "temperature" not in request.json
    assert request.json["messages"][-1] == {"role": "user", "content": "Say hello"}


async def test_the_run_node_button_sends_the_model_from_the_prompt_node(fake: FakeApiyi) -> None:
    prompt_node = create_node("ApiyiPrompt", model="deepseek-v4.1-flash")
    agent = _agent()
    other_node = create_node("ApiyiFlux", prompt="must not run")
    _connect(prompt_node, "prompt_model_config", agent, "model")
    _set(agent, "prompt", "Say hello")

    result = await current_engine().ahandle_request(ResolveNodeRequest(node_name=agent.name))

    assert isinstance(result, ResolveNodeResultSuccess), result.result_details
    assert agent.get_parameter_value("output") == CHAT_REPLY
    assert [request.path for request in fake.posts] == ["/v1/chat/completions"]
    assert fake.last_post.json["model"] == "deepseek-v4.1-flash"
    assert other_node.parameter_output_values.get("image") is None


async def test_the_agent_output_can_drive_an_image_node(fake: FakeApiyi) -> None:
    prompt_node = create_node("ApiyiPrompt", model="gpt-6-luna")
    agent = _agent()
    image_node = create_node("ApiyiGptImage")
    _connect(prompt_node, "prompt_model_config", agent, "model")
    _connect(agent, "exec_out", image_node, "exec_in")
    _connect(agent, "output", image_node, "prompt")
    _set(agent, "prompt", "Write an image prompt")

    await _run_flow()

    assert [request.path for request in fake.posts] == ["/v1/chat/completions", "/v1/images/generations"]
    assert fake.last_post.json["prompt"] == CHAT_REPLY
    assert image_node.parameter_output_values["was_successful"] is True


def test_a_reopened_workflow_needs_no_griptape_cloud_credential() -> None:
    prompt_node = create_node("ApiyiPrompt", model="gpt-6-luna")
    agent = _agent()
    _connect(prompt_node, "prompt_model_config", agent, "model")

    reopened_agent = _copy_and_paste(prompt_node, agent)

    assert reopened_agent.validate_before_workflow_run() is None


async def test_a_saved_workflow_reopens_and_chats_without_a_griptape_cloud_credential(
    fake: FakeApiyi, tmp_path: Path
) -> None:
    prompt_node = create_node("ApiyiPrompt", model="gpt-6-luna")
    agent = _agent()
    _connect(prompt_node, "prompt_model_config", agent, "model")
    _set(agent, "prompt", "Say hello")
    engine = current_engine()

    saved = await engine.ahandle_request(SaveWorkflowRequest(file_name="apiyi_saved"))
    assert isinstance(saved, SaveWorkflowResultSuccess), saved.result_details
    workflow_file = next((tmp_path / "workspace").rglob("apiyi_saved.py"))
    assert API_KEY not in workflow_file.read_text(encoding="utf-8")

    loaded = await engine.ahandle_request(RunWorkflowFromScratchRequest(file_path=str(workflow_file)))
    assert isinstance(loaded, RunWorkflowFromScratchResultSuccess), loaded.result_details
    reopened = next(
        node
        for node in engine.object_manager.get_filtered_subset(type=BaseNode).values()
        if type(node).__name__ == "Agent"
    )

    assert reopened.validate_before_workflow_run() is None
    started = await engine.ahandle_request(
        StartFlowRequest(flow_name=engine.node_manager.get_node_parent_flow_by_name(reopened.name))
    )
    assert isinstance(started, StartFlowResultSuccess), started.result_details
    assert fake.last_post.path == "/v1/chat/completions"
    assert fake.last_post.json["model"] == "gpt-6-luna"


def _copy_and_paste(*nodes: BaseNode) -> BaseNode:
    """Copy nodes and paste them, which applies their saved values and connections as a load does.

    Returns the pasted Agent.
    """
    engine = current_engine()
    before = set(engine.object_manager.get_filtered_subset(type=BaseNode))
    with engine.context_manager.flow(FLOW_NAME):
        copied = engine.handle_request(
            SerializeSelectedNodesToCommandsRequest(nodes_to_serialize=[[node.name, "0"] for node in nodes])
        )
        assert isinstance(copied, SerializeSelectedNodesToCommandsResultSuccess), copied.result_details
        pasted = engine.handle_request(
            DeserializeSelectedNodesFromCommandsRequest(
                deserialize_commands=copied.serialized_selected_node_commands,
                pickled_values=copied.pickled_values,
            )
        )
    assert isinstance(pasted, DeserializeSelectedNodesFromCommandsResultSuccess), pasted.result_details
    new_names = set(engine.object_manager.get_filtered_subset(type=BaseNode)) - before
    new_nodes = [engine.node_manager.get_node_by_name(name) for name in new_names]
    return next(node for node in new_nodes if type(node).__name__ == "Agent")
