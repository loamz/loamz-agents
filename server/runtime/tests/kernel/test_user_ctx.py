"""UserCtx: who is asking, validated at the edge and carried to every tool and sub-agent."""

from __future__ import annotations

import pytest

from loamz.runtime.kernel.types.run import RunScope
from loamz.runtime.kernel.types.user import UserCtx


def test_a_user_context_is_validated_and_equal_contexts_compare_equal() -> None:
    a = UserCtx.of(
        "ana", "acme", groups=["/b", "/a", "/a"], attributes={"loamz.data/workspace": "w1"}
    )
    b = UserCtx.of("ana", "acme", groups=("/a", "/b"), attributes={"loamz.data/workspace": "w1"})
    assert a == b and a.groups == ("/a", "/b")
    assert a.attribute("loamz.data/workspace") == "w1" and a.attribute("x/y") is None


@pytest.mark.parametrize(
    "bad",
    [
        {"subject": "", "tenant": "acme"},
        {"subject": "ana", "tenant": ""},
        {"subject": "a\nb", "tenant": "acme"},
        {"subject": "ana", "tenant": "t" * 300},
    ],
)
def test_a_missing_or_malformed_identity_is_refused(bad: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        UserCtx(**bad)


def test_attributes_must_be_namespaced_and_bounded() -> None:
    with pytest.raises(ValueError, match="namespaced"):
        UserCtx.of("ana", "acme", attributes={"workspace": "w1"})
    with pytest.raises(ValueError):
        UserCtx.of("ana", "acme", attributes={f"a/{i}": "v" for i in range(33)})


def test_the_scope_carries_the_user_context_to_a_child_with_its_attributes() -> None:
    user = UserCtx.of("ana", "acme", groups=["/finance"], attributes={"loamz.data/workspace": "w1"})
    scope = RunScope.from_user(user, thread_id="t1")
    assert (scope.tenant_id, scope.user_id, scope.groups) == ("acme", "ana", ("/finance",))
    assert scope.user == user

    child = RunScope.from_metadata(
        scope.child_metadata(), thread_id="t1", agent_id="c", agent_label="c"
    )
    assert child.user == user, "a sub-agent acts for the same person, with the same attributes"


def test_a_scope_without_a_person_has_no_user() -> None:
    assert RunScope().user is None
    assert RunScope(tenant_id="acme").user is None
