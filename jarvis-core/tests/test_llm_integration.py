

async def test_a_model_the_server_does_not_have_is_named_at_boot(caplog) -> None:
    """The failure M40 shipped: a stored model name outliving its namespace.

    Putting the gateway in front renamed every model, and an `llm.model` an
    operator had chosen in the console went on pointing at the old one. Every
    turn then came back as a 400 from the proxy — a log line that means
    nothing unless you built the proxy.
    """
    import logging

    from jarvis.integrations.llm import _probe_model_server

    class _Client:
        model = "qwen3.8-27b"

        async def list_models(self):
            return ["house", "house-fast"]

    class _Agent:
        model = "qwen3.8-27b"

    with caplog.at_level(logging.ERROR):
        await _probe_model_server(_Client(), "http://gateway:4000/v1", _Agent())
    said = " ".join(record.getMessage() for record in caplog.records)
    assert "qwen3.8-27b" in said
    assert "house" in said, "it has to say what IS available, not only what is not"


async def test_a_server_with_no_model_list_is_not_accused(caplog) -> None:
    """An empty list is "it does not answer that question", not "it has none"."""
    import logging

    from jarvis.integrations.llm import _probe_model_server

    class _Client:
        async def list_models(self):
            return []

    class _Agent:
        model = "whatever"

    with caplog.at_level(logging.ERROR):
        await _probe_model_server(_Client(), "http://elsewhere/v1", _Agent())
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
