# ha-config — Home Assistant side of Jarvis

Jarvis rides on your existing HA install; these files are *packages* you drop
in, plus the persona prompt you paste into the Ollama conversation agent.

## Install

1. Enable packages in `configuration.yaml` (if not already):

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

2. Copy `packages/jarvis/` into `/config/packages/jarvis/`, and
   `generated/jarvis_tools.yaml` (after running the generator) into the same
   dir. Search the files for `<<<` markers and adapt entity ids (person,
   notify service, satellites, phone BT sensor).

3. Add the secrets from `secrets.yaml.example` to `/config/secrets.yaml`.

4. Persona: Settings → Devices & services → Ollama → your `qwen3:8b` agent →
   Configure → paste `prompts/jarvis_system_prompt.txt` into the
   Instructions/prompt field. Enable "Assist" control of the home, streaming
   on. Name the agent **Jarvis** (`conversation.jarvis`).

5. Pipeline: Settings → Voice assistants → Add — name **Jarvis**;
   STT: your Wyoming sherpa/whisper (10300); Conversation agent: Jarvis;
   TTS: Wyoming Piper (10200), voice `en_GB-alan-medium`; Wake word:
   openWakeWord `hey_jarvis` (10400) for satellites.

6. Expose tools: Settings → Voice assistants → Expose — expose your ~40
   scripts/entities as before, plus the new Jarvis tool scripts:
   `script.jarvis_get_user_context`, `script.jarvis_run_background_task`,
   `script.jarvis_delegate_to_agents`, `script.jarvis_code_task`,
   `script.jarvis_code_task_status`, `script.jarvis_apply_code`,
   `script.jarvis_execute_command`, and each generated `script.<tool>`.
   Do NOT expose `script.jarvis_request_approval` or `script.jarvis_report`
   (internal).

7. Restart HA. Verify with Developer tools → Actions →
   `script.jarvis_get_user_context` (should return a context object).

## MCP tools

Settings → Integrations → **Model Context Protocol** → add your MCP server's
SSE URL; its tools join the agent automatically. stdio servers: front with
`mcp-proxy`.

## Files

| file | contents |
|---|---|
| `packages/jarvis/jarvis_context.yaml` | `get_user_context` tool + deterministic `jarvis_report` routing |
| `packages/jarvis/jarvis_background.yaml` | Tier-2 `run_background_task` + dispatcher automation |
| `packages/jarvis/jarvis_orchestrator.yaml` | Tier-3 tools + the human-approval gate (read the security notes inside) |
| `packages/jarvis/jarvis_retention.yaml` | nightly recorder purge + last-satellite tracking |
| `prompts/jarvis_system_prompt.txt` | persona + routing + safety rules |
| `generated/` | output of `jarvis_tools/generate_config.py` |
