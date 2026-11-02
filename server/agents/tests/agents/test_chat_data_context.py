"""A chat may say which Loamz Data workspace it is about and which catalogs the person chose; the run's user carries both."""

import pytest
from pydantic import ValidationError

from loamz.runtime.data.tools import DataConnection
from loamz.runtime.kernel.types.run import RunScope
from loamz.server.agents.features.conversations.chat.model import ChatRequest

WS = "9cce7e59-e1c0-4f8b-ab20-ff93a79161ca"


def test_the_workspace_and_chosen_catalogs_become_the_runs_user_attributes() -> None:
    request = ChatRequest(
        thread_id="t", data={"workspace": WS, "catalogs": ["shop", "nyc", "shop"]}
    )
    assert request.data is not None
    attributes = request.data.attributes()
    assert attributes == {"loamz.data/workspace": WS, "loamz.data/catalogs": "nyc,shop"}
    # What the service puts in the message metadata is what the runtime reads back as the person's attributes.
    scope = RunScope.from_metadata(
        {"tenant_id": "acme", "user_id": "ana", "user_attributes": attributes},
        thread_id="t",
        agent_id="a",
        agent_label="a",
    )
    assert scope.user is not None
    assert scope.user.attribute("loamz.data/workspace") == WS
    assert DataConnection.catalogs_of(scope.user) == frozenset({"nyc", "shop"})


def test_a_chat_without_data_or_without_catalogs_is_not_narrowed() -> None:
    assert ChatRequest(thread_id="t").data is None
    only_workspace = ChatRequest(thread_id="t", data={"workspace": WS}).data
    assert only_workspace is not None and only_workspace.attributes() == {
        "loamz.data/workspace": WS
    }


@pytest.mark.parametrize(
    "data",
    [
        {"workspace": "not a workspace id"},
        {"workspace": WS, "catalogs": ["Shop"]},
        {"workspace": WS, "catalogs": ["a b"]},
        {"workspace": WS, "catalogs": ["shop;drop"]},
        {"workspace": WS, "catalogs": [f"c{i}" for i in range(17)]},
        {"workspace": WS, "catalogs": ["a" * 63] * 5},
    ],
)
def test_a_bad_workspace_or_catalog_name_is_refused(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(thread_id="t", data=data)
