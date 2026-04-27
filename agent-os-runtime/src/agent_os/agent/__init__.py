__all__ = ["get_agent", "get_reasoning_agent", "new_session_id"]


def __getattr__(name: str):
    if name in __all__:
        from agent_os.agent import factory

        return getattr(factory, name)
    raise AttributeError(name)
