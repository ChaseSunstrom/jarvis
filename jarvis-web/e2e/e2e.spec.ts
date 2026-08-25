import { test, expect, type Page } from "@playwright/test";

/**
 * Put `light.lab_lights` back to `off`, whatever the last test left it as.
 *
 * The mock backend keeps one world for the whole run, so a test that toggles a
 * light leaves it toggled. Two tests below assert on the OFF state — one on the
 * pill, one on the toggle's `aria-label` — and both were passing only because
 * the tests that flip it happened to flip it back. Add a test anywhere before
 * them, or have one time out midway through its own restore step, and they fail
 * on a light nobody in that test ever touched. CI found exactly that.
 *
 * So they set the state they need instead of assuming it. Reading first rather
 * than clicking blindly keeps it idempotent, which matters because Playwright
 * may retry a test in the same worker against the world the failed attempt left.
 */
async function ensureLabLightOff(
  page: import("@playwright/test").Page,
): Promise<void> {
  const pill = page.getByTestId("state-light.lab_lights");
  await expect(pill).toBeVisible({ timeout: 15_000 });
  if ((await pill.textContent())?.trim() === "on") {
    await page.getByTestId("toggle-light.lab_lights").click();
  }
  await expect(pill).toHaveText("off", { timeout: 10_000 });
}

// Full round trip against the built app + mock backend (see serve-e2e.mjs):
// page load -> mic opens by itself -> /ws proxy -> mock pipeline events ->
// transcript + streamed response rendered in the DOM. Nothing is clicked.
test("a turn runs with nothing clicked, and renders transcript and response", async ({
  page,
}) => {
  const consoleLatencies: string[] = [];
  page.on("console", (msg) => {
    if (msg.text().includes("[jarvis] latencies"))
      consoleLatencies.push(msg.text());
  });

  await page.goto("/?e2e=1");
  // Nothing is clicked. There is no push-to-talk any more: the HUD opens its
  // microphone with the page and the VAD starts the turn, so the turn starting
  // by itself IS the assertion. (?e2e=1 stands in for the microphone headless
  // Chromium does not have, and still auto-stops the capture after 1.5 s.)
  //
  // No STANDBY gate ahead of it either — that existed to stop a click landing
  // before the handlers were bound, and there is no click. Waiting for STANDBY
  // now would be a race against the run it is supposed to precede.
  await expect(page.getByTestId("mic")).toBeVisible({ timeout: 10_000 });

  await expect(page.getByTestId("transcript")).toContainText(
    "turn on the lab lights",
    {
      timeout: 15_000,
    },
  );
  await expect(page.getByTestId("response")).toContainText(
    "Turning on the lab lights.",
    {
      timeout: 15_000,
    },
  );

  // latency readout shows measured timings
  await expect(page.getByTestId("latency")).toContainText("stt", {
    timeout: 10_000,
  });

  // no pipeline error surfaced
  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("healthz endpoint responds", async ({ request }) => {
  const res = await request.get("/healthz");
  expect(res.status()).toBe(200);
  expect(await res.json()).toEqual({ status: "ok" });
});

// /api/tts attaches the server-held admin token to whatever it fetches, so its
// allow-list is a security boundary. A `path.includes('..')` test is not one:
// the URL parser collapses %2e%2e too, so the encoded form below used to return
// 200 with the backend's token-protected payload.
test("the tts proxy only reaches media paths", async ({ request }) => {
  // The real thing still works.
  const good = await request.get("/api/tts?path=/api/tts_proxy/test.mp3");
  expect(good.status()).toBe(200);
  expect(good.headers()["content-type"]).toContain("audio");

  for (const path of [
    "/api/tts_proxy/../../_test/protected", // literal
    "/api/tts_proxy/%252e%252e/%252e%252e/_test/protected", // percent-encoded
    "/api/tts_proxy/%252E%252E/%252E%252E/_test/protected",
    "/api/tts_proxy/.%252e/.%252e/_test/protected",
    "/_test/protected",
    "//127.0.0.1:1/api/tts_proxy/a.wav",
  ]) {
    const res = await request.get(`/api/tts?path=${path}`);
    expect(res.status(), path).toBe(400);
    expect(await res.text(), path).not.toContain("admin-only-payload");
  }
});

// --- management UI ---------------------------------------------------------

test("console nav links the HUD to the four destinations", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("console-link").click();
  // The console link lands in HOUSE, which redirects to its first section.
  await expect(page).toHaveURL(/\/house\/devices$/);

  // Four destinations, not eleven (M48). Each lands on its own first section,
  // because a destination's own path is a redirect and never a second page.
  for (const [testid, path] of [
    ["nav-work", "/work/tasks"],
    ["nav-knowledge", "/knowledge/notes"],
    ["nav-settings", "/settings/assistant"],
    ["nav-house", "/house/devices"],
  ] as const) {
    await page.getByTestId(testid).click();
    await expect(page).toHaveURL(new RegExp(`${path}$`));
  }

  // and back to the voice HUD
  await page.getByTestId("hud-link").click();
  await expect(page.getByTestId("mic")).toBeVisible();
});

test("devices page groups entities by area and a toggle round-trips call_service", async ({
  page,
}) => {
  await page.goto("/house/devices");

  // Grouped under the area the entity registry puts it in.
  const lab = page.getByTestId("area-lab");
  await expect(lab).toBeVisible({ timeout: 15_000 });
  await expect(lab).toContainText("Lab");
  await expect(lab.getByTestId("entity-light.lab_lights")).toHaveCount(1);
  await expect(page.getByTestId("entity-light.lab_lights")).toContainText(
    "Lab Lights",
  );

  // Entities whose area comes from their device land in the same bucket.
  await expect(lab.getByTestId("entity-sensor.lab_temperature")).toHaveCount(1);
  // Areas the registry knows are rendered even for non-device entities.
  await expect(page.getByTestId("area-garage")).toContainText("Garage Door");

  await ensureLabLightOff(page);
  const pill = page.getByTestId("state-light.lab_lights");

  // click -> call_service over the ws relay -> mock mutates -> state_changed
  // arrives on the subscribe_events subscription -> DOM updates.
  await page.getByTestId("toggle-light.lab_lights").click();
  await expect(pill).toHaveText("on", { timeout: 10_000 });
  await expect(page.getByTestId("toggle-light.lab_lights")).toHaveText(
    "TURN OFF",
  );

  await page.getByTestId("toggle-light.lab_lights").click();
  await expect(pill).toHaveText("off", { timeout: 10_000 });

  // cover buttons and the climate setpoint use the same path
  await page.getByTestId("open-cover.garage_door").click();
  await expect(page.getByTestId("state-cover.garage_door")).toHaveText("open", {
    timeout: 10_000,
  });
  await page.getByTestId("close-cover.garage_door").click();
  await expect(page.getByTestId("state-cover.garage_door")).toHaveText(
    "closed",
    {
      timeout: 10_000,
    },
  );

  await page.getByTestId("play-media_player.speaker").click();
  await expect(page.getByTestId("state-media_player.speaker")).toHaveText(
    "playing",
    {
      timeout: 10_000,
    },
  );
  await page.getByTestId("pause-media_player.speaker").click();
  await expect(page.getByTestId("state-media_player.speaker")).toHaveText(
    "paused",
    {
      timeout: 10_000,
    },
  );

  await expect(page.getByTestId("error")).toHaveCount(0);

  // filtering narrows the grouped list
  await page.getByTestId("filter").fill("garage");
  await expect(page.getByTestId("entity-cover.garage_door")).toBeVisible();
  await expect(page.getByTestId("entity-light.lab_lights")).toHaveCount(0);
});

test("the devices page shows the phones and desktops running Jarvis", async ({
  page,
}) => {
  // Distinct from the entity list below it: these are the machines on the
  // other end of the socket. Nothing showed them before, so you could grant
  // your phone forty capabilities and never confirm it had connected.
  await page.goto("/house/devices");
  const panel = page.getByTestId("companions");
  await expect(panel).toBeVisible({ timeout: 15_000 });

  await expect(page.getByTestId("companion-pixel-8")).toContainText("Pixel 8");
  await expect(page.getByTestId("companion-pixel-8")).toContainText("android");
  await expect(page.getByTestId("companion-actions-pixel-8")).toContainText(
    "48",
  );
  await expect(page.getByTestId("companion-state-pixel-8")).toHaveText(
    "online",
  );

  // Offline is the state worth being able to see.
  await expect(page.getByTestId("companion-state-workshop-desktop")).toHaveText(
    "offline",
  );

  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("areas page creates, renames and deletes an area", async ({ page }) => {
  await page.goto("/areas");
  await expect(page.getByTestId("area-lab")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("new-area-name").fill("Test Bay");
  await page.getByTestId("create-area").click();
  const bay = page.getByTestId("area-test_bay");
  await expect(bay).toBeVisible({ timeout: 10_000 });

  await page.getByTestId("rename-test_bay").fill("Test Bay Two");
  await page.getByTestId("save-test_bay").click();
  await expect(bay).toContainText("Test Bay Two", { timeout: 10_000 });

  // assigning an entity moves it out of the unassigned bucket
  await page
    .getByTestId("assign-automation.night_mode")
    .selectOption({ value: "test_bay" });
  await expect(bay.getByTestId("assign-automation.night_mode")).toHaveCount(1, {
    timeout: 10_000,
  });

  await page.getByTestId("delete-test_bay").click();
  await expect(bay).toHaveCount(0, { timeout: 10_000 });
  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("automations page shows last_triggered, toggles and runs now", async ({
  page,
}) => {
  await page.goto("/automations");
  const row = page.getByTestId("automation-automation.night_mode");
  await expect(row).toBeVisible({ timeout: 15_000 });
  await expect(row).toContainText("Night Mode");

  await expect(page.getByTestId("last-automation.morning_lights")).toHaveText(
    "never",
  );
  await expect(page.getByTestId("state-automation.night_mode")).toHaveText(
    "on",
  );

  await page.getByTestId("toggle-automation.night_mode").click();
  await expect(page.getByTestId("state-automation.night_mode")).toHaveText(
    "off",
    {
      timeout: 10_000,
    },
  );
  await page.getByTestId("toggle-automation.night_mode").click();
  await expect(page.getByTestId("state-automation.night_mode")).toHaveText(
    "on",
    {
      timeout: 10_000,
    },
  );

  // An automation that can reach a lock says so before you run it, and one
  // that cannot does not — a badge on everything would say nothing.
  await expect(page.getByTestId("gated-automation.night_mode")).toBeVisible();
  await expect(page.getByTestId("gated-automation.morning_lights")).toHaveCount(
    0,
  );

  await page.getByTestId("trigger-automation.morning_lights").click();
  await expect(page.getByTestId("flash")).toContainText("triggered", {
    timeout: 10_000,
  });
  await expect(
    page.getByTestId("last-automation.morning_lights"),
  ).not.toHaveText("never", {
    timeout: 10_000,
  });
  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("a held action can be approved from the console, on any page", async ({
  page,
}) => {
  // The console had no way to answer an approval at all: the gate fired, the
  // model was told to wait, and only the phone could say yes.
  await page.goto("/house/devices");
  await expect(page.getByTestId("entity-light.lab_lights")).toBeVisible({
    timeout: 15_000,
  });

  // Raise one the way the assistant would — the backend fires
  // `jarvis_approval_required` when a tier-3 tool is held.
  const raise = async (tool: string, id: string) =>
    page.evaluate(
      ([t, rid]) =>
        new Promise((resolve) => {
          const ws = new WebSocket(
            `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`,
          );
          ws.onopen = () =>
            ws.send(
              JSON.stringify({
                id: 99,
                type: "test/raise_approval",
                tool: t,
                request_id: rid,
              }),
            );
          ws.onmessage = () => {
            ws.close();
            resolve(null);
          };
        }),
      [tool, id],
    );

  await raise("lock_control", "req-1");

  const banner = page.getByTestId("approvals");
  await expect(banner).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("approval-lock_control")).toBeVisible();
  // What is being agreed to must be on screen — the request was pinned to
  // concrete entity ids server-side, and this is where a human sees them.
  await expect(page.getByTestId("approval-args-lock_control")).toContainText(
    "lock.front_door",
  );

  // It must survive navigation: the action is still waiting whatever page you
  // wander to, and an approval that expires unseen looks like Jarvis ignoring
  // you.
  await page.getByTestId("nav-house").click();
  await expect(page.getByTestId("approvals")).toBeVisible();

  await page.getByTestId("approve-lock_control").click();
  await expect(page.getByTestId("approvals")).toHaveCount(0, {
    timeout: 10_000,
  });

  // And denying works, and is not the same as approving.
  await raise("lock_control", "req-2");
  await expect(page.getByTestId("approvals")).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("deny-lock_control").click();
  await expect(page.getByTestId("approvals")).toHaveCount(0, {
    timeout: 10_000,
  });

  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("tools page creates, edits and deletes a tool, and protects the built-ins", async ({
  page,
}) => {
  await page.goto("/tools");
  await expect(page.getByTestId("tool-lock_control")).toBeVisible({
    timeout: 15_000,
  });
  // A built-in offers no way to change it.
  await expect(page.getByTestId("tool-builtin-lock_control")).toBeVisible();
  await expect(page.getByTestId("tool-delete-lock_control")).toHaveCount(0);

  await page.getByTestId("tool-new").click();
  const editor = page.getByTestId("tool-editor-new");
  await expect(editor).toBeVisible();

  // A name a built-in already holds is refused — shadowing it would let the
  // assistant call something else while the logs still said `lock_control`.
  await editor.getByTestId("tool-field-name").fill("lock_control");
  await editor.getByTestId("tool-field-description").fill("Not what it says");
  await editor.getByTestId("tool-field-url").fill("http://evil.test/x");
  await editor.getByTestId("tool-save").click();
  await expect(editor.getByTestId("tool-form-error")).toContainText(
    "already a tool",
  );

  // A name the model could not say is refused before the wire.
  await editor.getByTestId("tool-field-name").fill("Has Spaces");
  await editor.getByTestId("tool-save").click();
  await expect(editor.getByTestId("tool-form-error")).toContainText(
    "lowercase",
  );

  await editor.getByTestId("tool-field-name").fill("paperless_search");
  await editor
    .getByTestId("tool-field-description")
    .fill("Search the document archive");
  await editor
    .getByTestId("tool-field-url")
    .fill("http://paperless.lan/api?q={{ query }}");
  await editor.getByTestId("tool-save").click();

  const created = page.getByTestId("tool-paperless_search");
  await expect(created).toBeVisible({ timeout: 10_000 });
  await expect(created).toContainText("Search the document archive");

  // Edit: the name is fixed, because the model calls it by that word.
  await page.getByTestId("tool-edit-paperless_search").click();
  const open = page.getByTestId("tool-editor-paperless_search");
  await expect(open.getByTestId("tool-field-name")).toBeDisabled();
  await open.getByTestId("tool-field-description").fill("Search Paperless");
  await open.getByTestId("tool-save").click();
  await expect(created).toContainText("Search Paperless", { timeout: 10_000 });

  const del = page.getByTestId("tool-delete-paperless_search");
  await del.click();
  await expect(del).toHaveText("CONFIRM?");
  await del.click();
  await expect(created).toHaveCount(0, { timeout: 10_000 });

  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("settings page edits a setting, resets it, and is honest about restarts", async ({
  page,
}) => {
  await page.goto("/settings");
  const model = page.getByTestId("setting-llm.model");
  await expect(model).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("source-llm.model")).toHaveText("yaml");

  // SAVE is disabled until something actually changes — a button that always
  // looks clickable teaches people to click it and learn nothing.
  await expect(page.getByTestId("save-llm.model")).toBeDisabled();

  await page.getByTestId("input-llm.model").selectOption("qwen3:14b");
  await page.getByTestId("save-llm.model").click();
  await expect(page.getByTestId("source-llm.model")).toHaveText("overlay", {
    timeout: 10_000,
  });
  // A `live` setting must not claim a restart is needed.
  await expect(page.getByTestId("restart-needed")).toHaveCount(0);

  // A restart-only setting says so, rather than pretending to be in effect.
  await page.getByTestId("input-llm.timeout").fill("45");
  await page.getByTestId("save-llm.timeout").click();
  await expect(page.getByTestId("restart-needed")).toContainText(
    "llm.timeout",
    {
      timeout: 10_000,
    },
  );

  // A refused value reports against its own field, not the top of the page.
  await page.getByTestId("input-llm.options.temperature").fill("9");
  await page.getByTestId("save-llm.options.temperature").click();
  await expect(page.getByTestId("error-llm.options.temperature")).toContainText(
    "between",
  );

  // Reset puts the file's value back and drops the override.
  await page.getByTestId("reset-llm.model").click();
  await expect(page.getByTestId("source-llm.model")).toHaveText("yaml", {
    timeout: 10_000,
  });
  await expect(page.getByTestId("input-llm.model")).toHaveValue("qwen3:8b");

  // A package owns this one: no way to edit it, and the file to edit is named.
  await expect(page.getByTestId("input-jarvis.time_zone")).toBeDisabled();
  await expect(page.getByTestId("package-jarvis.time_zone")).toContainText(
    "packages/house.yaml",
  );

  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("automations page creates, edits and deletes an automation", async ({
  page,
}) => {
  await page.goto("/automations");
  await expect(
    page.getByTestId("automation-automation.night_mode"),
  ).toBeVisible({
    timeout: 15_000,
  });

  // --- create ---------------------------------------------------------
  await page.getByTestId("new").click();
  const editor = page.getByTestId("editor-new");
  await expect(editor).toBeVisible();

  // Save with a name but no trigger: the form must refuse before the wire,
  // and say which field is wrong rather than "invalid".
  await editor.getByTestId("field-alias").fill("Porch Light");
  await editor.getByTestId("field-trigger").fill("[]");
  await editor.getByTestId("save").click();
  await expect(editor.getByTestId("form-error")).toContainText(
    "at least one trigger",
  );

  // Malformed JSON is reported as such, naming the box it is in.
  await editor.getByTestId("field-trigger").fill("{not json");
  await editor.getByTestId("save").click();
  await expect(editor.getByTestId("form-error")).toContainText("trigger:");

  await editor
    .getByTestId("field-trigger")
    .fill('[{"platform":"time","at":"21:00:00"}]');
  await editor
    .getByTestId("field-action")
    .fill('[{"service":"light.turn_on"}]');
  await editor.getByTestId("save").click();

  const created = page.getByTestId("automation-automation.porch_light");
  await expect(created).toBeVisible({ timeout: 10_000 });
  await expect(created).toContainText("Porch Light");

  // --- edit -----------------------------------------------------------
  await page.getByTestId("edit-automation.porch_light").click();
  const openEditor = page.locator('[data-testid^="editor-ui_"]');
  await expect(openEditor.getByTestId("field-alias")).toHaveValue(
    "Porch Light",
  );
  // Loaded from the server, not from what was typed a moment ago.
  // toHaveValue, not toContainText: `bind:value` sets the property, so a
  // textarea's DOM text content stays empty however full the box looks.
  await expect(openEditor.getByTestId("field-trigger")).toHaveValue(/21:00:00/);

  await openEditor.getByTestId("field-alias").fill("Porch Light Two");
  await openEditor.getByTestId("save").click();
  await expect(created).toContainText("Porch Light Two", { timeout: 10_000 });

  // --- a YAML automation offers no way to change it --------------------
  await expect(page.getByTestId("yaml-automation.night_mode")).toBeVisible();
  await expect(page.getByTestId("edit-automation.night_mode")).toHaveCount(0);
  await expect(page.getByTestId("delete-automation.night_mode")).toHaveCount(0);

  // --- delete ----------------------------------------------------------
  const del = page.getByTestId("delete-automation.porch_light");
  await del.click();
  // One click arms, the second commits — an automation is recoverable only
  // by typing it again.
  await expect(del).toHaveText("CONFIRM?");
  await del.click();
  await expect(created).toHaveCount(0, { timeout: 10_000 });

  await expect(page.getByTestId("error")).toHaveCount(0);
});

/** Tell the mock to forget the toolbox commands, the way an older backend has. */
async function forgetToolbox(page: Page, unsupported: boolean): Promise<void> {
  await page.evaluate(
    (flag) =>
      new Promise((resolve) => {
        const ws = new WebSocket(
          `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`,
        );
        ws.onopen = () =>
          ws.send(
            JSON.stringify({
              id: 94,
              type: "jarvis/test/tools_unsupported",
              unsupported: flag,
            }),
          );
        ws.onmessage = () => {
          ws.close();
          resolve(null);
        };
      }),
    unsupported,
  );
}

test("tools page lists the model's own toolbox and test-runs a tool", async ({
  page,
}) => {
  // The native path — jarvis-core implements jarvis/tools/list and
  // jarvis/tools/call. This is what the user actually gets; the fallback
  // below is only for an older backend.
  await page.goto("/tools");
  await forgetToolbox(page, false);
  await page.reload();

  await expect(page.getByTestId("tool-select")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("hint")).toHaveCount(0);

  // A registry tool with no dot in its name — the exact shape whose fallback
  // used to re-throw "unknown command 'jarvis/tools/call'", because
  // splitToolName() needs a dot and code_task has none.
  await page.getByTestId("tool-select").selectOption("code_task");
  await page.getByTestId("tool-args").fill('{"repo": "x", "instruction": "y"}');
  await page.getByTestId("tool-run").click();
  await expect(page.getByTestId("tool-result")).toContainText("code_task", {
    timeout: 10_000,
  });
  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("a tier-3 tool asks before it runs, even from the test runner", async ({
  page,
}) => {
  // The console is authenticated, so it is tempting to let it just run things.
  // That would make this page the easiest Tier-3 bypass in the product.
  await page.goto("/tools");
  await forgetToolbox(page, false);
  await page.reload();
  await expect(page.getByTestId("tool-select")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("tool-select").selectOption("lock_control");
  // Said BEFORE the button is pressed, not after.
  await expect(page.getByTestId("tool-needs-approval")).toContainText("asks you first");

  await page.getByTestId("tool-args").fill('{"name": "front door"}');
  await page.getByTestId("tool-run").click();
  await expect(page.getByTestId("tool-result")).toContainText("approval_required", {
    timeout: 10_000,
  });
});

test("tools page degrades to the service catalogue on an older backend", async ({
  page,
}) => {
  await page.goto("/tools");
  await forgetToolbox(page, true);
  await page.reload();

  // The mock answers unknown_command for jarvis/tools/list — the page must
  // explain that and fall back rather than break.
  await expect(page.getByTestId("hint")).toContainText("jarvis/tools/list", {
    timeout: 15_000,
  });
  await expect(page.getByTestId("tool-light.turn_on")).toBeVisible();

  await page.getByTestId("tool-select").selectOption("switch.turn_on");
  await page.getByTestId("tool-args").fill('{"entity_id": "switch.desk_fan"}');
  await page.getByTestId("tool-run").click();
  await expect(page.getByTestId("tool-result")).toContainText(
    "changed_states",
    { timeout: 10_000 },
  );
  await expect(page.getByTestId("tool-result")).toContainText(
    "switch.desk_fan",
  );

  // bad JSON is reported, not thrown
  await page.getByTestId("tool-args").fill("{not json");
  await page.getByTestId("tool-run").click();
  await expect(page.getByTestId("error")).toContainText("not valid JSON");

  // exposure toggle writes through the entity registry
  const expose = page.getByTestId("expose-light.lab_lights");
  await expect(expose).toHaveText("EXPOSED");
  await expose.click();
  await expect(expose).toHaveText("HIDDEN", { timeout: 10_000 });
  await expose.click();
  await expect(expose).toHaveText("EXPOSED", { timeout: 10_000 });

  // Put it back: the suite shares one mock process, and a leaked flag would
  // make whichever test runs next fail for a reason that is not its own.
  await forgetToolbox(page, false);
});

test("settings page reports the selected backend and streams events", async ({
  page,
}) => {
  await page.goto("/settings");

  // serve-e2e.mjs points JARVIS_URL at the mock and HA_URL at a dead port, so
  // seeing the mock's url here proves JARVIS_* took precedence.
  await expect(page.getByTestId("backend-kind")).toHaveText("core", {
    timeout: 15_000,
  });
  await expect(page.getByTestId("backend-url")).toContainText("127.0.0.1");
  await expect(page.getByTestId("backend-token")).toContainText(
    "held server-side",
  );
  await expect(page.getByTestId("config-problem")).toHaveCount(0);
  // The pipeline is read-only and says so. It used to be a `<select>` whose
  // value could not be committed anywhere, next to a second, read-only copy
  // of a TTS voice the editable Voice group above already owns.
  await expect(page.getByTestId("pipeline-name")).toContainText("Jarvis");
  await expect(page.getByTestId("tts-voice")).toHaveCount(0);
  // ...and the voice is edited exactly once, in the group that can save it.
  await expect(page.getByTestId("setting-voice.tts_voice")).toBeVisible();

  // The event stream is a diagnostic now, folded away rather than sitting
  // open below the settings people came to change.
  const stream = page.getByTestId("event-stream");
  await expect(stream).not.toHaveAttribute("open", /.*/);
  await stream.getByText("Event stream").click();
  await expect(stream).toHaveAttribute("open", /.*/);

  // the live event stream fills once something moves
  await expect(page.getByTestId("live-filter")).toHaveText("state_changed");
  const page2 = await page.context().newPage();
  await page2.goto("/house/devices");
  await page2.getByTestId("toggle-switch.desk_fan").click({ timeout: 15_000 });
  await expect(page.getByTestId("event-log")).toContainText("switch.desk_fan", {
    timeout: 15_000,
  });
  await page2.getByTestId("toggle-switch.desk_fan").click();
  await page2.close();
});

// --- chrome: boot sequence, motion, palette, shortcuts, toasts -------------

test("the boot sequence plays, never blocks a click, and runs once per session", async ({
  page,
}) => {
  // `commit` returns before hydration, so the poll below starts early enough
  // to catch a 1.2 s overlay instead of racing it.
  await page.goto("/house/devices", { waitUntil: "commit" });
  await expect(page.getByTestId("boot")).toBeAttached({ timeout: 10_000 });

  // The precise claim is "pointer-events: none": while the overlay is on
  // screen, a hit test at the nav's centre must still land on the nav. Simply
  // clicking would not prove it — Playwright would happily wait the animation
  // out and then click.
  const hit = await page.evaluate(() => {
    const nav = document.querySelector(
      '[data-testid="nav-settings"]',
    ) as HTMLElement | null;
    if (!nav) return { bootUp: false, hitsNav: false };
    const r = nav.getBoundingClientRect();
    const top = document.elementFromPoint(
      r.x + r.width / 2,
      r.y + r.height / 2,
    );
    return {
      bootUp: Boolean(document.querySelector('[data-testid="boot"]')),
      hitsNav: Boolean(top && nav.contains(top)),
    };
  });
  expect(hit.bootUp).toBe(true);
  expect(hit.hitsNav).toBe(true);

  // It dissolves on its own and marks the session.
  await expect(page.getByTestId("boot")).toHaveCount(0, { timeout: 10_000 });
  expect(
    await page.evaluate(() => sessionStorage.getItem("jarvis:boot-played")),
  ).toBe("1");

  // And does not replay on the next load in the same session.
  await page.reload();
  await expect(page.getByTestId("nav-house")).toBeVisible();
  await expect(page.getByTestId("boot")).toHaveCount(0);
});

test("prefers-reduced-motion skips the boot sequence entirely", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/house/devices", { waitUntil: "commit" });
  await expect(page.getByTestId("nav-house")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByTestId("boot")).toHaveCount(0);
  // Not merely "gone quickly" — never shown at all.
  await page.waitForTimeout(400);
  await expect(page.getByTestId("boot")).toHaveCount(0);
  await page.emulateMedia({ reducedMotion: null });
});

test("route changes swap the console body and mark the current nav item", async ({
  page,
}) => {
  await page.goto("/house/devices");
  const route = page.getByTestId("route");
  await expect(route).toHaveAttribute("data-route", "/house/devices");

  // The DESTINATION is lit while you are anywhere inside it. It is a prefix
  // match: a destination's own path redirects to its first section, so the
  // user is never at `/house` — they are at `/house/devices` — and an exact
  // match left every tab unlit the moment the consolidation landed.
  await expect(page.getByTestId("nav-house")).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByTestId("nav-work")).not.toHaveAttribute(
    "aria-current",
    "page",
  );

  // Switching SECTION keeps the destination lit and swaps the body.
  await page.getByTestId("section-automations").click();
  await expect(route).toHaveAttribute("data-route", "/house/automations");
  // The section's own probe, not a heading: a section no longer repeats its
  // name under a tab that already says it.
  await expect(page.getByTestId("automations-screen")).toBeVisible();
  await expect(page.getByTestId("nav-house")).toHaveAttribute(
    "aria-current",
    "page",
  );

  // Switching DESTINATION moves the mark.
  await page.getByTestId("nav-work").click();
  await expect(page.getByTestId("nav-work")).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByTestId("nav-house")).not.toHaveAttribute(
    "aria-current",
    "page",
  );

  // The transition wrapper is the thing the animation hangs off; it must
  // actually be in the tree, not an idea in a stylesheet.
  await expect(route).toHaveClass(/jv-route/);
});

test("the connection indicator reports the real websocket state", async ({
  page,
}) => {
  await page.goto("/house/devices");
  await expect(page.getByTestId("link-status")).toHaveAttribute(
    "data-status",
    "connected",
    {
      timeout: 15_000,
    },
  );
  await expect(page.getByTestId("link-status")).toContainText("LINK OK");
});

test("the command palette opens from the keyboard, filters, and toggles an entity", async ({
  page,
}) => {
  await page.goto("/house/devices");
  await expect(page.getByTestId("link-status")).toHaveAttribute(
    "data-status",
    "connected",
    {
      timeout: 15_000,
    },
  );
  await ensureLabLightOff(page);

  await page.keyboard.press("Control+k");
  await expect(page.getByTestId("palette")).toBeVisible();

  // Centred, and inside the viewport. `translateX(-50%)` plus an entrance
  // animation that ends on `transform: none` is an easy way to lose this.
  const box = (await page.getByTestId("palette").boundingBox())!;
  const viewport = page.viewportSize()!;
  expect(box.x).toBeGreaterThan(0);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
  expect(Math.abs(box.x + box.width / 2 - viewport.width / 2)).toBeLessThan(2);

  // Esc closes it again.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("palette")).toHaveCount(0);

  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("lab lights");
  await expect(
    page.getByTestId("palette-item-entity:light.lab_lights"),
  ).toBeVisible();
  // Filtering is exclusive, not just a highlight.
  await expect(
    page.getByTestId("palette-item-entity:cover.garage_door"),
  ).toHaveCount(0);
  await expect(page.getByTestId("palette-hint")).toContainText("turn on");

  // Enter on a flippable entity performs the call and closes the palette.
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("palette")).toHaveCount(0);
  await expect(page.getByTestId("state-light.lab_lights")).toHaveText("on", {
    timeout: 10_000,
  });
  await expect(page.getByTestId("toast").first()).toContainText("Lab Lights");

  // Put the world back, as a courtesy rather than as a contract — the tests
  // that need it off now say so themselves, because this step is skipped
  // entirely if anything above it fails.
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("lab lights");
  await expect(page.getByTestId("palette-hint")).toContainText("turn off");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("state-light.lab_lights")).toHaveText("off", {
    timeout: 10_000,
  });
});

test("the command palette jumps to a page", async ({ page }) => {
  await page.goto("/house/devices");
  // data-status only becomes "connected" from client code, so this doubles as
  // "the page has hydrated" — a keystroke sent before that lands nowhere.
  await expect(page.getByTestId("link-status")).toHaveAttribute(
    "data-status",
    "connected",
    {
      timeout: 15_000,
    },
  );
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("settings");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/settings\/assistant$/);
});

test("keyboard shortcuts focus the filter and navigate", async ({ page }) => {
  await page.goto("/house/devices");
  await expect(page.getByTestId("link-status")).toHaveAttribute(
    "data-status",
    "connected",
    {
      timeout: 15_000,
    },
  );
  await expect(page.getByTestId("filter")).toBeVisible();

  await page.keyboard.press("/");
  expect(
    await page.evaluate(() =>
      document.activeElement?.getAttribute("data-testid"),
    ),
  ).toBe("filter");
  // `/` focused the field rather than being typed into it.
  await expect(page.getByTestId("filter")).toHaveValue("");

  // A bare letter inside a text field must stay a letter.
  await page.keyboard.type("gd");
  await expect(page).toHaveURL(/\/devices$/);
  await expect(page.getByTestId("filter")).toHaveValue("gd");

  await page.getByTestId("filter").fill("");
  await page.keyboard.press("Escape");
  await page.keyboard.press("g");
  await page.keyboard.press("a");
  await expect(page).toHaveURL(/\/automations$/);

  await page.keyboard.press("g");
  await page.keyboard.press("d");
  await expect(page).toHaveURL(/\/devices$/);
});

// The mock has a lock entity but no `lock` domain in its service catalogue —
// exactly the shape of "the UI offers a control the backend cannot perform".
// It used to fail in silence.
test("a rejected call_service raises a toast as well as an inline error", async ({
  page,
}) => {
  await page.goto("/house/devices");
  const lock = page.getByTestId("lock-lock.front_door");
  await expect(lock).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("filter").fill("front door");
  await expect(lock).toBeVisible();

  await lock.click();

  const toast = page.getByTestId("toast").first();
  await expect(toast).toBeVisible({ timeout: 10_000 });
  await expect(toast).toContainText("Lock failed");
  await expect(toast).toContainText("unknown service lock.lock");
  await expect(page.getByTestId("error")).toContainText(
    "unknown service lock.lock",
  );

  // It is dismissible, not just decorative.
  await page.getByTestId("toast-dismiss").first().click();
  await expect(page.getByTestId("toast")).toHaveCount(0);
});

test("the console is usable at phone width without sideways scrolling", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/house/devices");
  await expect(page.getByTestId("entity-light.lab_lights")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("nav-house")).toBeVisible();
  await expect(page.getByTestId("toggle-light.lab_lights")).toBeVisible();

  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);

  // html { overflow-x: hidden } would hide a genuine overflow from the check
  // above, so the header cluster is measured directly: nothing may be clipped
  // off the right edge.
  for (const id of ["link-status", "palette-open", "filter"]) {
    const box = (await page.getByTestId(id).boundingBox())!;
    expect(box.x + box.width, id).toBeLessThanOrEqual(391);
    expect(box.x, id).toBeGreaterThanOrEqual(-1);
  }

  // The palette has to fit too — it is the phone's main way around.
  await page.getByTestId("palette-open").click();
  const box = (await page.getByTestId("palette").boundingBox())!;
  expect(box.width).toBeLessThanOrEqual(390);
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  await page.keyboard.press("Escape");
});

test("every management editor fits a phone, which is where the app shows them", async ({
  page,
}) => {
  // The Android app's Manage screen is a WebView onto this console, so these
  // editors ARE the phone UI. An editor that overflows is unusable there in a
  // way it never is on a desktop, and nothing else in this suite would notice.
  await page.setViewportSize({ width: 390, height: 844 });

  const noOverflow = async (where: string) => {
    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    );
    expect(overflow, where).toBeLessThanOrEqual(1);
  };

  // Automations: open the create form, which has the widest content (three
  // JSON textareas).
  await page.goto("/automations");
  await page.getByTestId("new").click();
  await expect(page.getByTestId("editor-new")).toBeVisible({ timeout: 15_000 });
  await noOverflow("automations · new");
  for (const id of ["field-alias", "field-trigger", "save"]) {
    const box = (await page.getByTestId(id).boundingBox())!;
    expect(box.x + box.width, `automations · ${id}`).toBeLessThanOrEqual(391);
    expect(box.x, `automations · ${id}`).toBeGreaterThanOrEqual(-1);
  }

  // Tools: same shape, more fields.
  await page.goto("/tools");
  await page.getByTestId("tool-new").click();
  await expect(page.getByTestId("tool-editor-new")).toBeVisible({
    timeout: 15_000,
  });
  await noOverflow("tools · new");
  const url = (await page.getByTestId("tool-field-url").boundingBox())!;
  expect(url.x + url.width).toBeLessThanOrEqual(391);

  // Settings: a row per setting, each with a control and two buttons — the
  // most likely thing to wrap badly.
  await page.goto("/settings");
  await expect(page.getByTestId("setting-llm.model")).toBeVisible({
    timeout: 15_000,
  });
  await noOverflow("settings");
  for (const id of ["input-llm.model", "save-llm.model"]) {
    const box = (await page.getByTestId(id).boundingBox())!;
    expect(box.x + box.width, `settings · ${id}`).toBeLessThanOrEqual(391);
  }

  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("keyboard focus is visible, and icon-only controls are labelled", async ({
  page,
}) => {
  await page.goto("/house/devices");
  await expect(page.getByTestId("link-status")).toHaveAttribute(
    "data-status",
    "connected",
    {
      timeout: 15_000,
    },
  );
  // The label assertion at the end reads "turn on", so the light has to be off.
  await ensureLabLightOff(page);

  // The first tab stop is the skip link, and it is drawn.
  await page.keyboard.press("Tab");
  const focus = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el) return null;
    const s = getComputedStyle(el);
    return {
      cls: el.className,
      text: el.textContent?.trim(),
      outlineWidth: parseFloat(s.outlineWidth),
      outlineStyle: s.outlineStyle,
    };
  });
  expect(focus?.cls).toContain("jv-skip");
  expect(focus?.text).toBe("Skip to content");
  // A focus ring that renders as 0px or `none` is not a focus ring.
  expect(focus!.outlineWidth).toBeGreaterThanOrEqual(2);
  expect(focus!.outlineStyle).not.toBe("none");

  // Buttons whose label is a glyph still say what they do.
  await expect(page.getByTestId("prev-media_player.speaker")).toHaveAttribute(
    "aria-label",
    /previous track/i,
  );
  await expect(page.getByTestId("next-media_player.speaker")).toHaveAttribute(
    "aria-label",
    /next track/i,
  );
  await expect(page.getByTestId("toggle-light.lab_lights")).toHaveAttribute(
    "aria-label",
    /turn on lab lights/i,
  );
});

test("the console header stays put when the page scrolls", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 480 });
  await page.goto("/house/devices");
  await expect(page.getByTestId("entity-light.lab_lights")).toBeVisible({
    timeout: 15_000,
  });

  await page.evaluate(() => window.scrollTo(0, 900));
  await page.waitForTimeout(200);
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(200);

  // `overflow-x: hidden` on the root is one careless line away from turning the
  // document into a scroll container and quietly breaking `position: sticky`.
  //
  // Measured against the HEADER's own box, not a fixed 80px. The number was a
  // proxy for "the header is one row tall", which is not what this test is
  // about: the eleventh section (`/desktop`) made the header wrap to two rows
  // at 1280px and this went red while `position: sticky` was working
  // perfectly. Making the nav scroll instead of wrap fixes the height and
  // hides SETTINGS behind an invisible scroll, which is worse — so the header
  // is two rows for now (M48 owns the nav's real overflow answer) and this
  // asserts the thing in its own name.
  const header = (await page.locator(".console-top").boundingBox())!;
  expect(header.y).toBe(0);
  const nav = (await page.getByTestId("nav-house").boundingBox())!;
  expect(nav.y).toBeGreaterThanOrEqual(0);
  expect(nav.y + nav.height).toBeLessThanOrEqual(header.y + header.height);
  const badge = (await page.getByTestId("link-status").boundingBox())!;
  expect(badge.y).toBeGreaterThanOrEqual(0);
  expect(badge.y + badge.height).toBeLessThanOrEqual(header.y + header.height);
});

// The tab icon. Committing a favicon proves nothing on its own — it has to be
// served under the path app.html asks for, and the browser has to be able to
// decode it. A 404 or a malformed SVG both show up as the generic globe, which
// is exactly what this replaced.
test("the arc reactor is served as the tab icon", async ({ page, request }) => {
  for (const [path, type] of [
    ["/favicon.svg", "image/svg+xml"],
    ["/favicon.ico", "icon"],
    ["/apple-touch-icon.png", "image/png"],
  ] as const) {
    const res = await request.get(path);
    expect(res.status(), path).toBe(200);
    expect(res.headers()["content-type"], path).toContain(type);
    expect((await res.body()).length, path).toBeGreaterThan(500);
  }

  await page.goto("/");
  // The links survive SvelteKit's app.html templating: `%sveltekit.assets%`
  // must have been substituted, not shipped literally.
  const hrefs = await page
    .locator('link[rel="icon"], link[rel="apple-touch-icon"]')
    .evaluateAll((nodes) =>
      nodes.map((n) => (n as HTMLLinkElement).getAttribute("href") ?? ""),
    );
  expect(hrefs.some((h) => h.endsWith("/favicon.svg"))).toBe(true);
  expect(hrefs.some((h) => h.endsWith("/favicon.ico"))).toBe(true);
  expect(hrefs.some((h) => h.endsWith("/apple-touch-icon.png"))).toBe(true);
  expect(hrefs.some((h) => h.includes("%sveltekit"))).toBe(false);

  // Chromium decodes it: a malformed SVG resolves with naturalWidth 0, and a
  // blocked one (the CSP's img-src) never resolves at all.
  const decoded = await page.evaluate(
    () =>
      new Promise<{ ok: boolean; w: number }>((resolve) => {
        const img = new Image();
        img.onload = () => resolve({ ok: true, w: img.naturalWidth });
        img.onerror = () => resolve({ ok: false, w: 0 });
        img.src = "/favicon.svg";
      }),
  );
  expect(decoded.ok).toBe(true);
  expect(decoded.w).toBe(64);
});

test("a turn shows every tool it calls, with real progress and a reason when one fails", async ({
  page,
}) => {
  // The bug this replaces: a turn that called four tools and took nine seconds
  // rendered a spinner. Tool calls are the moment the assistant touches the
  // house, and they were the one thing the console never showed.
  await page.goto("/house/devices");
  // Wait for the page's own connection to be live before asking the mock to
  // broadcast — an event fired before the layout subscribes reaches nobody,
  // and the test would fail for a reason that has nothing to do with the panel.
  await expect(page.getByTestId("entity-light.lab_lights")).toBeVisible({
    timeout: 15_000,
  });

  // Ask the mock to run a round of four, with the third one failing. A second
  // socket is how the rest of this suite drives the backend: the page's own
  // client is not reachable from here, and reaching into it would test the
  // test rather than the console.
  await page.evaluate(
    (names) =>
      new Promise((resolve) => {
        const ws = new WebSocket(
          `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`,
        );
        ws.onopen = () =>
          ws.send(
            JSON.stringify({
              id: 98,
              type: "jarvis/test/tool_run",
              tools: names,
              fail_at: 2,
            }),
          );
        ws.onmessage = () => {
          ws.close();
          resolve(null);
        };
      }),
    ["get_state", "turn_on", "lock_control", "set_temperature"],
  );

  const panel = page.getByTestId("tool-activity");
  await expect(panel).toBeVisible({ timeout: 10_000 });

  // Every call is named. A summary that said "4 tools" would not tell you
  // which one touched the lock.
  for (const name of [
    "get_state",
    "turn_on",
    "lock_control",
    "set_temperature",
  ]) {
    await expect(page.getByTestId(`tool-row-${name}`)).toBeVisible();
  }

  // The progress is the model's own count, not a timer.
  await expect(page.getByTestId("tool-progress-count")).toHaveText("4 / 4", {
    timeout: 10_000,
  });
  const bar = panel.getByRole("progressbar");
  await expect(bar).toHaveAttribute("aria-valuenow", "100");

  // The failure keeps its reason, and is not drawn as a success.
  await expect(page.getByTestId("tool-error-lock_control")).toHaveText(
    "no such entity",
  );
  // ...and the calls that worked are not tarred with it.
  await expect(page.getByTestId("tool-error-turn_on")).toHaveCount(0);

  // The panel is transient: it clears itself rather than leaving a stale
  // record of a turn that finished a minute ago sitting over the house.
  await expect(panel).toHaveCount(0, { timeout: 20_000 });
});

test("a phone that registers while the console is open appears without a reload", async ({
  page,
}) => {
  // The report: "I registered my android device, but the web app still doesn't
  // recognize it as a device." Two halves — the phone never sent its register
  // frame through the relay (fixed in the app), and this half: the console
  // read the companion list exactly once, at mount, so a device that arrived
  // a minute later was invisible for as long as the tab stayed open.
  await page.goto("/house/devices");
  await expect(page.getByTestId("companion-pixel-8")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("companion-late-phone")).toHaveCount(0);

  await page.evaluate(
    () =>
      new Promise((resolve) => {
        const ws = new WebSocket(
          `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`,
        );
        ws.onopen = () =>
          ws.send(
            JSON.stringify({
              id: 97,
              type: "jarvis/test/register_companion",
              device_id: "late-phone",
              name: "Late Phone",
            }),
          );
        ws.onmessage = () => {
          ws.close();
          resolve(null);
        };
      }),
  );

  await expect(page.getByTestId("companion-late-phone")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByTestId("companion-state-late-phone")).toHaveText(
    "online",
  );
  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("Jarvis can ask a question and the answer reaches the server", async ({
  page,
}) => {
  // The assistant needs facts only the user has — the address of a service on
  // their network, which of three lamps they meant. Without a way to ask it
  // guesses, and a guess about an address is a request sent to the wrong host.
  //
  // A question rides the approval gate rather than a second channel, so it
  // inherits single use, an expiry and human-only resolution. What it does not
  // inherit is the words: "APPROVE / DENY" is the wrong pair for "which lamp?".
  await page.goto("/house/devices");
  await expect(page.getByTestId("entity-light.lab_lights")).toBeVisible({
    timeout: 15_000,
  });

  const ask = async (payload: Record<string, unknown>) =>
    page.evaluate(
      (body) =>
        new Promise((resolve) => {
          const ws = new WebSocket(
            `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`,
          );
          ws.onopen = () => ws.send(JSON.stringify({ id: 96, ...body }));
          ws.onmessage = () => {
            ws.close();
            resolve(null);
          };
        }),
      payload,
    );

  const lastAnswer = async (): Promise<string | null> =>
    page.evaluate(
      () =>
        new Promise<string | null>((resolve) => {
          const ws = new WebSocket(
            `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`,
          );
          ws.onopen = () =>
            ws.send(
              JSON.stringify({ id: 95, type: "jarvis/test/last_answer" }),
            );
          ws.onmessage = (ev) => {
            ws.close();
            resolve(JSON.parse(ev.data as string)?.result?.answer ?? null);
          };
        }),
    );

  // --- free text -----------------------------------------------------------
  await ask({
    type: "jarvis/test/ask_user",
    request_id: "ask-1",
    question: "What is the printer's URL?",
  });

  await expect(page.getByTestId("question-ask_user")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByTestId("question-text")).toHaveText(
    "What is the printer's URL?",
  );
  // It is a question, so it does not offer APPROVE.
  await expect(page.getByTestId("approve-ask_user")).toHaveCount(0);

  // Nothing to send until something is typed.
  await expect(page.getByTestId("answer-send")).toBeDisabled();
  await page.getByTestId("answer-input").fill("http://printer.lan");
  await page.getByTestId("answer-send").click();

  await expect(page.getByTestId("question-ask_user")).toHaveCount(0, {
    timeout: 10_000,
  });
  expect(await lastAnswer()).toBe("http://printer.lan");

  // --- choices -------------------------------------------------------------
  await ask({
    type: "jarvis/test/ask_user",
    request_id: "ask-2",
    question: "Which lamp did you mean?",
    choices: ["Desk", "Corner", "Ceiling"],
  });

  await expect(page.getByTestId("question-choices")).toBeVisible({
    timeout: 10_000,
  });
  // A knowable set of answers is buttons, not a box to type one of three
  // words into and misspell.
  await expect(page.getByTestId("answer-input")).toHaveCount(0);
  await page.getByTestId("answer-choice-Corner").click();

  await expect(page.getByTestId("question-ask_user")).toHaveCount(0, {
    timeout: 10_000,
  });
  expect(await lastAnswer()).toBe("Corner");

  // --- a question from a turn that read somebody else's words --------------
  //
  // The tier system answers "may this run without a human". It cannot answer
  // "should the human believe the words on the screen" — and for a question,
  // unlike an action, what is shown IS the model's own sentence. A turn that
  // read a hostile page can write "confirm your password" in Jarvis's voice.
  // Nothing is blocked, because a turn that read a page and needs to ask which
  // result was meant is the legitimate case. The human is told where the words
  // came from.
  await ask({
    type: "jarvis/test/ask_user",
    request_id: "ask-tainted",
    question: "Please confirm your bank password",
    tainted: true,
  });
  await expect(page.getByTestId("question-tainted")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByTestId("question-tainted")).toContainText("untrusted");
  await page.getByTestId("answer-dismiss").click();
  await expect(page.getByTestId("question-ask_user")).toHaveCount(0, {
    timeout: 10_000,
  });

  // ...and an ordinary question carries no such warning.
  await ask({
    type: "jarvis/test/ask_user",
    request_id: "ask-plain",
    question: "Which lamp?",
  });
  await expect(page.getByTestId("question-ask_user")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByTestId("question-tainted")).toHaveCount(0);
  await page.getByTestId("answer-dismiss").click();
  await expect(page.getByTestId("question-ask_user")).toHaveCount(0, {
    timeout: 10_000,
  });

  // --- dismissing ----------------------------------------------------------
  await ask({
    type: "jarvis/test/ask_user",
    request_id: "ask-3",
    question: "Still there?",
  });
  await expect(page.getByTestId("question-ask_user")).toBeVisible({
    timeout: 10_000,
  });
  await page.getByTestId("answer-dismiss").click();
  await expect(page.getByTestId("question-ask_user")).toHaveCount(0, {
    timeout: 10_000,
  });

  await expect(page.getByTestId("error")).toHaveCount(0);
});

/** What the console's own password is set to, once, by the first test to need it. */
const CONSOLE_PASSWORD = "e2e-console-password";
/** Mirrors JARVIS_PAIRING_SECRET on the mock backend (see mock-ha.mjs). */
const PAIRING_SECRET = "e2e-pairing-secret";

/**
 * Get past the console password, from whichever side of it this run starts.
 *
 * The hash outlives the run — it is a file under `.storage/` — so the first
 * ever run chooses the password and every run after it types the same one.
 * Asserting one of those two states would make this suite pass once and then
 * fail forever on the same machine, which is the same trap `ensureLabLightOff`
 * exists for.
 */
async function unlockConsole(
  page: import("@playwright/test").Page,
): Promise<void> {
  await expect(page.getByTestId("pairing")).toBeVisible({ timeout: 15_000 });
  const unlocked = page.getByTestId("pair-unlocked");
  const field = page.getByTestId("pair-password");
  await expect(unlocked.or(field).first()).toBeVisible({ timeout: 15_000 });
  if (await unlocked.count()) return;
  await field.fill(CONSOLE_PASSWORD);
  await page.getByTestId("pair-unlock").click();
  await expect(unlocked).toBeVisible({ timeout: 15_000 });
}

test("the console shows a pairing QR, and what it encodes is a code and not a token", async ({
  page,
}) => {
  // Forty characters of base64 typed on a phone keyboard is the worst moment
  // of setting Jarvis up. The obvious shortcut — put the token in the QR — is
  // worse than typing it: a QR on a screen can be photographed from across the
  // room and stays valid as long as the token does. So the QR carries a
  // short-lived, single-use code the app exchanges for a token.
  await page.goto("/settings");
  const panel = page.getByTestId("pairing");
  await expect(panel).toBeVisible({ timeout: 15_000 });

  // Nothing on screen until it is asked for: a code that appears whenever
  // somebody opens Settings is a code sitting on a screen in an empty room.
  await expect(page.getByTestId("pair-qr")).toHaveCount(0);

  // The address defaults to the origin this page is served on, which is
  // demonstrably an address that reaches Jarvis.
  await expect(page.getByTestId("pair-url")).toHaveValue(/^https?:\/\//);

  // Minting needs something the relay does not already have. Anything that can
  // reach this console can use its admin token — the relay attaches it to
  // whatever connects — so the token alone must not be enough to make a
  // permanent credential out of transient reach. Until the password is proved
  // server-side there is nothing to press.
  await expect(page.getByTestId("pair-new")).toBeDisabled();
  await unlockConsole(page);

  // The console has to hold the pairing secret to mint on the operator's
  // behalf. Handed over once, to the SERVER — and one jarvis-core refuses is
  // dropped again, so the field to correct it comes back rather than leaving
  // GENERATE failing at somebody with nowhere to type.
  if (await page.getByTestId("pair-secret-form").count()) {
    await page.getByTestId("pair-secret").fill("wrong-secret");
    await page.getByTestId("pair-secret-save").click();
    await expect(page.getByTestId("pair-error")).toContainText("not correct", {
      timeout: 10_000,
    });
    await expect(page.getByTestId("pair-qr")).toHaveCount(0);
    await expect(page.getByTestId("pair-secret-form")).toBeVisible();

    await page.getByTestId("pair-secret").fill(PAIRING_SECRET);
    await page.getByTestId("pair-secret-save").click();
  }
  await expect(page.getByTestId("pair-qr")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("pair-qr").locator("svg")).toHaveCount(1);

  // The payload is the format `PairingPayload.kt` parses, and what it carries
  // is a code.
  const payload = await page.getByTestId("pair-payload").textContent();
  expect(payload).toMatch(/^jarvis:\/\/pair\?v=1&u=/);
  expect(payload).toContain("&c=mock-code-");
  // And emphatically not a token. This is the assertion the whole design is for.
  expect(payload).not.toContain("paired-token");

  await expect(page.getByTestId("pair-expiry")).toContainText("s left");

  await page.getByTestId("pair-hide").click();
  await expect(page.getByTestId("pair-qr")).toHaveCount(0);

  // One press. Not a re-typed secret and not a re-typed password: that is the
  // whole point of proving it once per session, and the difference between
  // adding a device being a click and being a hunt for the secret.
  await expect(page.getByTestId("pair-new")).toBeEnabled();
  await page.getByTestId("pair-new").click();
  await expect(page.getByTestId("pair-qr")).toBeVisible({ timeout: 10_000 });

  // And a reload does not ask for either again, so the code on screen is there
  // before anybody has touched the keyboard. What survives the reload is an
  // httpOnly cookie — nothing page JavaScript can read back.
  await page.reload();
  await expect(page.getByTestId("pairing")).toBeVisible();
  await expect(page.getByTestId("pair-qr")).toBeVisible({ timeout: 10_000 });
  expect(
    await page.evaluate(() => JSON.stringify(sessionStorage)),
  ).not.toContain(PAIRING_SECRET);

  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("the pairing secret is shown only after the password, and only from the server", async ({
  page,
}) => {
  // The secret is what stops reach to this port being enough to mint a
  // permanent credential. So the reveal is the one place it may travel, and it
  // must not be in the page before the button that asks for it: a control that
  // fetched it on load and hid it behind a `{#if}` has already handed it over
  // to anything that can read the DOM.
  await page.goto("/settings");
  await unlockConsole(page);
  if (await page.getByTestId("pair-secret-form").count()) {
    await page.getByTestId("pair-secret").fill(PAIRING_SECRET);
    await page.getByTestId("pair-secret-save").click();
  }
  // A fresh load, so nothing in this page was typed into it: whatever is here
  // is what the server sent, and the secret is not part of it. Asserted on the
  // ROW rather than on the button, so a page that arrived with the secret
  // already in hand fails this line rather than failing to find a control.
  await page.reload();
  await expect(page.getByTestId("pair-secret-row")).toBeVisible({
    timeout: 15_000,
  });
  expect(await page.content()).not.toContain(PAIRING_SECRET);

  await page.getByTestId("pair-reveal").click();
  await expect(page.getByTestId("pair-secret-value")).toHaveText(
    PAIRING_SECRET,
    { timeout: 10_000 },
  );

  // Locking puts it away again, both on screen and on the server: the button
  // is for the operator walking away from the machine.
  await page.getByTestId("pair-relock").click();
  await expect(page.getByTestId("pair-secret-value")).toHaveCount(0);
  await expect(page.getByTestId("pair-new")).toBeDisabled();
  expect(await page.content()).not.toContain(PAIRING_SECRET);
});

test("a paired device can be un-paired, and the panel says what is connected", async ({
  page,
}) => {
  // Pairing without un-pairing is a one-way door. A phone that is lost, sold
  // or no longer trusted has to be removable, and until this existed the only
  // way was editing the token store by hand on the server.
  await page.goto("/settings");
  const panel = page.getByTestId("tokens");
  await expect(panel).toBeVisible({ timeout: 15_000 });

  // Built from the auth manager, so everything it knows about appears —
  // including the credential this console is using.
  await expect(page.getByTestId("token-tok-console")).toContainText("console");
  await expect(page.getByTestId("token-state-tok-console")).toHaveText(
    "connected now",
  );
  await expect(page.getByTestId("token-state-tok-oldphone")).toHaveText(
    "not connected",
  );

  await page.getByTestId("token-revoke-tok-oldphone").click();
  await expect(page.getByTestId("token-tok-oldphone")).toHaveCount(0, {
    timeout: 10_000,
  });
  // The one still in use is untouched — revoking is per row, not a purge.
  await expect(page.getByTestId("token-tok-console")).toBeVisible();

  await expect(page.getByTestId("error")).toHaveCount(0);
});

test("the console password is checked on the server, and guessing at it is bounded", async ({
  request,
}) => {
  // Both halves of the gate, asked without a browser — because the browser is
  // not where either of them is enforced. A page that merely hides the button
  // is not a password; a `curl` loop is the attacker this exists for.
  const reveal = await request.post("/api/pair/secret");
  expect(reveal.status()).toBe(401);
  expect(await reveal.text()).not.toContain(PAIRING_SECRET);

  // And minting, which is the half that makes a PERMANENT credential.
  const mint = await request.post("/api/pair");
  expect(mint.status()).toBe(401);

  const status = await (await request.get("/api/console")).json();
  expect(status.authenticated).toBe(false);
  if (!status.configured) {
    // Only when this test is run on its own: the pairing tests above choose it
    // otherwise, and the guesses below have to be wrong ones rather than the
    // choice itself.
    expect(
      (await request.post("/api/console", { data: { password: CONSOLE_PASSWORD } })).ok(),
    ).toBe(true);
  }

  // An unlimited password endpoint is not a password. scrypt costs an attacker
  // tens of milliseconds a guess, which is worth nothing if they may have as
  // many guesses as they like: every password a person would actually choose
  // falls inside an afternoon, in silence. This deliberately spends this
  // client's whole allowance, which is why it is the last test in the file.
  let refusedAt = 0;
  for (let i = 1; i <= 8; i += 1) {
    const res = await request.post("/api/console", {
      data: { password: `not-the-password-${i}` },
    });
    if (res.status() === 429) {
      refusedAt = i;
      break;
    }
    expect(res.status(), "a wrong password must be refused, not accepted").toBe(401);
  }
  expect(refusedAt, "the endpoint answered eight guesses without ever saying no").toBeGreaterThan(
    0,
  );
});


// The mute is the only voice control the HUD has, and the whole design rests on
// it: a microphone that opens with the page and cannot be closed is not
// something to ship. So this asserts the two things that make it real rather
// than decorative — that it survives a reload, and that a muted page does not
// start a turn even when everything else would have.
test("the microphone can be muted, and stays muted across a reload", async ({
  page,
}) => {
  await page.goto("/?e2e=1");
  const mic = page.getByTestId("mic");
  await expect(mic).toBeVisible({ timeout: 10_000 });
  await expect(mic).toContainText(/listening/i, { timeout: 10_000 });
  // Unmuted, a turn happens on its own — the same claim the round-trip test
  // makes, restated here so the muted case below is a contrast and not just an
  // absence.
  await expect(page.getByTestId("transcript")).toContainText(
    "turn on the lab lights",
    { timeout: 15_000 },
  );

  await mic.click();
  await expect(mic).toHaveAttribute("aria-pressed", "true");
  await expect(mic).toContainText(/muted/i);

  // Reload muted: the transcript must stay empty for longer than the unmuted
  // turn above took to appear, or this proves nothing about the mute and only
  // that reloading is slow.
  await page.reload();
  await expect(page.getByTestId("mic")).toHaveAttribute("aria-pressed", "true");
  await page.waitForTimeout(3_000);
  await expect(page.getByTestId("transcript")).toHaveText("");

  // And it comes back. Left muted, this would silently break every later run
  // in this worker, since the mute is remembered per origin.
  await page.getByTestId("mic").click();
  await expect(page.getByTestId("mic")).toHaveAttribute("aria-pressed", "false");
});


// The microphone opens with the page, and nothing else opens it.
//
// Deliberately NOT ?e2e=1: that mode starts a turn to stand in for the
// microphone the round-trip test cannot rely on, and starting a turn opens the
// microphone as a side effect — so on that page this would pass with the
// on-mount open deleted. On a plain load the only thing that can open it is
// the code under test.
//
// `data-mic` rather than the label, for the same reason: the label reads
// LISTENING whenever nothing has gone wrong, which includes when nothing has
// been tried. Chromium runs with a fake capture device (playwright.config.ts),
// so getUserMedia genuinely resolves and this is the real thing.
test("the microphone opens with the page, with nothing clicked", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByTestId("mic")).toHaveAttribute("data-mic", "open", {
    timeout: 15_000,
  });
});


// Inside the Android app's console frame, the page must not draw the frame's
// own nav a second time.
//
// ManagementActivity puts a native tab strip above this WebView and has to: a
// link tapped inside a WebView is a page-initiated navigation, and WebView does
// not attach `additionalHeaders` to those, so the page's nav cannot carry the
// bearer token. Two rows of tabs, one of which silently does not work.
//
// Driven by the User-Agent ManagementActivity actually sends, so this and the
// Kotlin cannot drift apart quietly — console_parity_test.py pins the same pair
// from the other side.
test("the console shows whose voice Jarvis answers, without the voiceprint reaching the browser", async ({
  page,
}) => {
  // The panel's whole security claim: "is somebody enrolled" must not also
  // answer "what do they sound like".
  //
  // Asserted on the RESPONSE rather than on the rendered HTML. The first
  // version of this checked that the page did not contain the word "vector"
  // and failed on the favicon comment two hundred lines above, which explains
  // why browsers prefer an SVG — a page-text search is the wrong instrument
  // for a payload claim.
  const payload = page.waitForResponse(
    (r) => r.url().includes("/api/voice/speaker") && r.request().method() === "GET",
  );
  await page.goto("/settings");
  const body = await (await payload).json();
  expect(body.enrolled).toBe(true);
  for (const key of ["vector", "vectors", "samples_data", "mean", "profile"]) {
    expect(body, `the voiceprint reached the browser as ${key}`).not.toHaveProperty(key);
  }
  // `samples` is a COUNT here, not the data. If it ever becomes an array, the
  // vectors have started travelling.
  expect(typeof body.samples).toBe("number");

  const panel = page.getByTestId("voice-identity");
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("speaker-mode")).toHaveText(/observe/);
  await expect(page.getByTestId("speaker-samples")).toContainText("5 of 20");
  // The owner's own worst sample, next to the threshold. Without it nobody can
  // tell whether enforcing would lock them out, which is the whole failure
  // this feature produces in practice.
  await expect(page.getByTestId("speaker-threshold")).toContainText("7.065");
});

/**
 * Deliberately does NOT unlock the console first.
 *
 * Two reasons, and the second one cost a full-suite run to find. Deleting a
 * voiceprint disables the gate that refuses strangers, and this relay attaches
 * the admin token to whatever asks it — so the refusal IS the feature, and
 * asserting it is worth more than asserting the happy path.
 *
 * And the happy path cannot be asserted here reliably anyway: the console
 * password test above deliberately exhausts the unlock rate limiter, which is
 * server-side and shared, so a later `unlockConsole` is refused with the
 * correct password. Passing in isolation and failing in the suite is exactly
 * how that presented. Ordering this test earlier would work today and break the
 * next time somebody moves a test, so it does not depend on order at all.
 *
 * The delete succeeding is covered where it can be checked deterministically:
 * `src/lib/server/routes.test.ts` proves the route consults `sessionValid`,
 * and `jarvis-core/tests/test_speaker_gate.py` proves the backend's DELETE
 * really clears the profile.
 */
test("forgetting a voiceprint is refused without the console password", async ({
  page,
}) => {
  await page.goto("/settings");
  await page.getByTestId("speaker-forget").click();
  await expect(page.getByTestId("speaker-error")).toContainText(/unlock the console/i);
  // Still enrolled: the refusal was real rather than cosmetic.
  await expect(page.getByTestId("speaker-samples")).toContainText("5 of 20");
});

test("the console drops its own nav when the Android app is framing it", async ({
  browser,
}) => {
  const framed = await browser.newContext({
    userAgent: "JarvisAndroid/1.0.0 (ai.jarvis.app; management)",
  });
  const page = await framed.newPage();
  await page.goto("/house/devices");

  // The page is there and working...
  await expect(page.getByTestId("area-lab")).toBeVisible({ timeout: 15_000 });
  // ...and its copy of the frame's chrome is not.
  await expect(page.getByTestId("nav-house")).toBeHidden();
  await expect(page.getByTestId("hud-link")).toBeHidden();
  await framed.close();

  // In an ordinary browser nothing is hidden — the console is the whole chrome
  // there, and a rule that hid it everywhere would pass the assertions above.
  const plain = await browser.newContext();
  const normal = await plain.newPage();
  await normal.goto("/house/devices");
  await expect(normal.getByTestId("nav-house")).toBeVisible({ timeout: 15_000 });
  await expect(normal.getByTestId("hud-link")).toBeVisible();
  await plain.close();
});

/**
 * Enrolling a voice from the browser.
 *
 * This page could always see the profile and delete it; creating one was the
 * gap, and the reason was a credential rather than a capability — the enrol
 * relay demands the caller's own Jarvis token and a browser has none. It now
 * also accepts an unlocked console session.
 *
 * Two things are worth asserting and they are different in kind. That the
 * phrases come from the SERVER is a correctness claim: the whole argument for a
 * second enrolment surface is that both read one list from one place, so a list
 * hard-coded in the component would quietly undo it. That an unauthenticated
 * browser is REFUSED is the security claim, and it is asserted the same way its
 * sibling above asserts FORGET — without unlocking, because the unlock limiter
 * is server-side and shared, and a test that depends on suite order is a test
 * that breaks when somebody moves one.
 */
test("the console offers enrolment, reading its phrases from the server", async ({
  page,
}) => {
  await page.goto("/settings");
  const panel = page.getByTestId("voice-identity");
  await expect(panel).toBeVisible();

  await page.getByTestId("enrol-start").click();

  // The phrases the mock serves in `prompts`, which mirror jarvis-core's
  // ENROLMENT_PROMPTS. Asserting the TEXT, so a component that shipped its own
  // copy would have to reproduce the server's list exactly to pass — at which
  // point it is the same list.
  await expect(page.getByTestId("enrol-record-0")).toBeVisible();
  await expect(panel).toContainText("Good evening, Jarvis. Bring the house up, would you?");
  await expect(panel).toContainText("One, two, three, four, five, six, seven, eight, nine, ten.");

  // A real progress bar, not a decorative div: it has to be announceable.
  const bar = page.getByTestId("enrol-progress");
  await expect(bar).toHaveAttribute("role", "progressbar");
  await expect(bar).toHaveAttribute("aria-valuenow", "0");

  // Against the SERVER's minimum, not the length of the list.
  await expect(page.getByTestId("enrol-remaining")).toContainText("3 more phrases");
});

test("enrolling from a locked console is refused, and says how to unlock it", async ({
  page,
  context,
}) => {
  // The fake microphone chromium is launched with produces a tone, so this
  // records real audio and really posts it — the refusal comes from the
  // relay, not from there being nothing to send.
  await context.grantPermissions(["microphone"]);
  await page.goto("/settings");
  await page.getByTestId("enrol-start").click();
  await page.getByTestId("enrol-record-0").click();
  await expect(page.getByTestId("enrol-stop-0")).toBeVisible();
  // Long enough to clear the client-side "that was a tap" guard, so what is
  // being tested is the server's answer rather than ours.
  await page.waitForTimeout(1200);
  await page.getByTestId("enrol-stop-0").click();

  // The relay's own words, which name BOTH credentials — better than the
  // component's fallback, and the reason the component prefers the server's
  // message to its own.
  await expect(page.getByTestId("enrol-detail-0")).toContainText(
    /needs the phone.s own Jarvis token, or the console password/i,
  );
  // And the phrase stays retryable rather than being consumed by the failure.
  await expect(page.getByTestId("enrol-record-0")).toHaveText("RETRY");
});
