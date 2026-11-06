"""Load the library into a real engine with empty config and secrets, next to a fake APIYI server."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import griptape_nodes.retained_mode.managers.config_manager as config_manager_module
import griptape_nodes.retained_mode.managers.secrets_manager as secrets_manager_module
import pytest
from griptape_nodes.exe_types.node_types import BaseNode
from griptape_nodes.node_library.library_registry import LibraryRegistry
from griptape_nodes.retained_mode.engine import current_engine, reset_root_engine
from griptape_nodes.retained_mode.events.base_events import ResultPayload
from griptape_nodes.retained_mode.events.execution_events import ExecuteNodeRequest
from griptape_nodes.retained_mode.events.flow_events import CreateFlowRequest, CreateFlowResultSuccess
from griptape_nodes.retained_mode.events.library_events import (
    RegisterLibraryFromFileRequest,
    RegisterLibraryFromFileResultSuccess,
)
from griptape_nodes.retained_mode.events.node_events import CreateNodeRequest, CreateNodeResultSuccess

from griptape_nodes_library_apiyi import apiyi_client

from .fake_apiyi import FakeApiyi

REPO_ROOT = Path(__file__).parent.parent
LIBRARY_JSON = REPO_ROOT / "griptape-nodes-library.json"
LIBRARY_NAME = json.loads(LIBRARY_JSON.read_text())["name"]
FLOW_NAME = "test_flow"
API_KEY = "sk-test-key"


@pytest.fixture
def fake() -> Iterator[FakeApiyi]:
    server = FakeApiyi()
    yield server
    server.close()


@pytest.fixture(autouse=True)
def engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeApiyi) -> Iterator[Any]:
    """Give each test a fresh engine. The developer's own config and secrets are never read."""
    reset_root_engine()
    LibraryRegistry._clear()
    for key in list(os.environ):
        if key.startswith(("GT_CLOUD_", "GTN_CONFIG_", "APIYI_")):
            monkeypatch.delenv(key, raising=False)

    config_path = tmp_path / "griptape_nodes_config.json"
    config_path.write_text(json.dumps({"workspace_directory": str(tmp_path / "workspace")}))
    env_path = tmp_path / ".env"
    env_path.write_text(f"APIYI_API_KEY={API_KEY}\nAPIYI_BASE_URL={fake.url}\n")
    monkeypatch.setattr(config_manager_module, "USER_CONFIG_PATH", config_path)
    monkeypatch.setattr(secrets_manager_module, "ENV_VAR_PATH", env_path)
    no_waiting = {status: (0,) * len(delays) for status, delays in apiyi_client.RETRY_DELAYS_SECONDS.items()}
    monkeypatch.setattr(apiyi_client, "RETRY_DELAYS_SECONDS", no_waiting)

    # Register a copy, so that registering the library writes nothing into the repo.
    library_dir = tmp_path / "library"
    shutil.copytree(
        REPO_ROOT / "griptape_nodes_library_apiyi",
        library_dir / "griptape_nodes_library_apiyi",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy(LIBRARY_JSON, library_dir / LIBRARY_JSON.name)

    current = current_engine()
    result = current.handle_request(RegisterLibraryFromFileRequest(file_path=str(library_dir / LIBRARY_JSON.name)))
    assert isinstance(result, RegisterLibraryFromFileResultSuccess), result.result_details
    current.context_manager.push_workflow(workflow_name="test_workflow")
    flow = current.handle_request(
        CreateFlowRequest(parent_flow_name=None, flow_name=FLOW_NAME, set_as_new_context=False)
    )
    assert isinstance(flow, CreateFlowResultSuccess), flow.result_details
    try:
        yield current
    finally:
        reset_root_engine()
        LibraryRegistry._clear()


@pytest.fixture
def no_api_key(engine: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the key. The engine copies secrets into os.environ when it starts, so both places need it."""
    (tmp_path / ".env").write_text("")
    monkeypatch.delenv("APIYI_API_KEY", raising=False)


def create_node(node_type: str, **values: Any) -> BaseNode:
    current = current_engine()
    result = current.handle_request(
        CreateNodeRequest(node_type=node_type, specific_library_name=LIBRARY_NAME, override_parent_flow_name=FLOW_NAME)
    )
    assert isinstance(result, CreateNodeResultSuccess), result.result_details
    node = current.node_manager.get_node_by_name(result.node_name)
    for name, value in values.items():
        node.set_parameter_value(name, value)
    return node


@dataclass
class Refused:
    """A node that validation stopped. It has the same two members as a failed result."""

    result_details: list[Exception]

    def succeeded(self) -> bool:
        return False


async def run_node(node: BaseNode) -> ResultPayload | Refused:
    """Validate, then run, in the order of a real flow. ExecuteNodeRequest alone skips validation."""
    problems = node.validate_before_node_run()
    if problems:
        return Refused(problems)
    return await current_engine().ahandle_request(ExecuteNodeRequest(node_name=node.name))
