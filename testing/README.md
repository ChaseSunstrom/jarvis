# testing/ — the end-to-end harness

The real `jarvis-core`, booted against fake model and voice backends, so any
client can be driven end to end with no GPU, no models and no hardware.

```bash
pip install -r jarvis-core/requirements.txt -r testing/requirements.txt
python3 -m pytest testing/e2e -q          # the suites
python3 testing/harness/harness.py --wait # keep one up for a device/emulator
```

| File | What it is |
|---|---|
| `harness/fake_ollama.py` | Ollama's `/api/tags` + NDJSON `/api/chat`, scripted. Stdlib only. |
| `harness/fake_wyoming.py` | whisper/piper/openWakeWord over the real Wyoming framing. Stdlib only. |
| `harness/harness.py` | Writes a config, starts all three processes, waits for `/healthz`, prints JSON. |
| `harness/client.py` | The async REST+websocket client the suites drive it with. |
| `harness/scripts/default.json` | The fake model's default brain. |
| `e2e/test_harness_selftest.py` | Proves all of the above against a real server. |
| `scripts/run-e2e.sh` | What CI calls; keeps logs and audio under `artifacts/`. |

Nothing here is mocked on the server side — a real `python -m jarvis` process
is listening on a real socket. Only the two things that would need a GPU are
replaced, and both are replaced at the wire protocol.

The full map, including what still needs real hardware:
[`docs/testing.md`](../docs/testing.md).
