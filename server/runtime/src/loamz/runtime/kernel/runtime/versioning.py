"""What a run is pinned to: the agent's own declared version, not the version of this package."""

DEFAULT_AGENT_VERSION = "1"


def can_replay(agent: object, pinned: str, current: str) -> bool:
    """Whether ``agent`` (at ``current``) may continue a run that began under ``pinned``.

    The same version always can. A different one can only if the agent says so with
    ``replays_from(pinned)``: it knows whether its new behaviour is compatible with what the old
    journal recorded, and the engine does not guess.
    """
    if pinned == current:
        return True
    decide = getattr(agent, "replays_from", None)
    return bool(decide(pinned)) if callable(decide) else False


__all__ = ["DEFAULT_AGENT_VERSION", "can_replay"]
