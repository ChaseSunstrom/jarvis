# Workflow recipes

Twelve workflows worth having, each written as a **brief you can build from**
rather than as JSON to paste. JSON goes stale the moment n8n bumps a node
version; a brief does not, and `list_n8n_node_types` tells you the versions
this instance actually has.

## How to use one

1. `list_n8n_node_types` — get the real type strings and versions.
2. Build the graph from the brief below, substituting those.
3. `check_n8n_workflow` — free, instant, catches an invented node.
4. `create_n8n_workflow` — it arrives switched off.
5. Tell the user exactly which credentials to attach, from
   `connections_needed`.
6. Later, `check_n8n_health` answers "did that actually work?".

Every brief names its **trigger**, its **steps**, the **credentials** a human
will have to attach, and the **thing that usually goes wrong**. That last
section is the valuable part — it is what turns a workflow that saves into a
workflow that runs.

---

## 1. Morning briefing into a message

**Why** The one everybody wants first, and the one that teaches the shape.

**Trigger** Schedule, daily, a fixed local time.

**Steps**
1. Schedule trigger.
2. Fetch each source in parallel — a calendar list for today, a weather API,
   an RSS feed or two.
3. A Code node that merges them into one short markdown string. Keep the
   formatting here, not in the sender: you will want to change the wording
   ten times and the sender never.
4. Send: email, Telegram, Slack, whatever they read in the morning.

**Credentials** the calendar; the sender.

**What usually goes wrong** The schedule's timezone. n8n uses the workflow's
`settings.timezone`, falling back to the instance's — not the user's. A
briefing that arrives at 07:00 UTC in a house on BST arrives at eight. Set the
timezone explicitly and say which one you set.

---

## 2. File a receipt from email into a sheet

**Why** The canonical "outside world" job, and the reason the n8n integration
exists at all.

**Trigger** Gmail/IMAP trigger on a label or a search — `has:attachment
subject:(receipt OR invoice)`.

**Steps**
1. Mail trigger, restricted by label. Never "all mail".
2. Filter: has a PDF or image attachment.
3. Extract the fields — an LLM node if the instance has one wired up, or a
   regex over the plain-text body for the common senders.
4. Append a row to a spreadsheet: date, vendor, amount, currency, a link back
   to the message.
5. Optionally, save the attachment to a drive folder named by year and month.

**Credentials** the mailbox; the spreadsheet; the drive.

**What usually goes wrong** Two things. Currency: a number scraped without
its symbol becomes a column that cannot be summed — capture the symbol or the
ISO code as its own field. And re-runs: mail triggers replay on restart, so
key the row on the message id and skip one that is already there, or a
restart doubles January.

---

## 3. Say something in the house when an outside thing happens

**Why** This is the join the house cannot make on its own, and it is the one
worth building carefully.

**Trigger** Whatever the outside thing is — a webhook, a mail arrival, a
schedule.

**Steps**
1. The trigger.
2. An HTTP Request node calling Jarvis:
   `POST http://<jarvis>:8080/api/services/notify/notify` with a bearer token
   and `{"message": "..."}`.
3. Nothing else. Resist adding logic here — the house's own rules belong in
   an automation, where they can be read and edited on the Automations page.

**Credentials** a Jarvis bearer token, as an n8n **credential**, never as a
literal in the node.

**What usually goes wrong** The token in a header field, in plain text, in a
workflow anyone with n8n access can read. Use an n8n credential of type
"Header Auth". And note Jarvis does not send node parameters to the model
precisely because this is where people put keys.

---

## 4. Weekly digest of anything that failed

**Why** Automations fail silently. This is the workflow that notices.

**Trigger** Schedule, weekly.

**Steps**
1. Schedule trigger.
2. `GET /api/v1/executions?status=error` on n8n's own API, for the last seven
   days.
3. Group by workflow, count.
4. Send one message: what failed, how often, and a link to each execution.

**Credentials** an n8n API key (n8n calling itself is fine and normal).

**What usually goes wrong** Reading execution DATA into the digest. Status,
count and timing are enough; the data is the contents of every message that
went through, and a digest containing it is a digest you would not want
forwarded.

---

## 5. Back up the things that are not backed up

**Trigger** Schedule, daily, in the small hours.

**Steps**
1. Schedule trigger.
2. Export what matters: `GET /api/v1/workflows` from n8n, a database dump via
   an HTTP endpoint or an SSH command node, a config directory as a tarball.
3. Write to object storage or a drive folder, named by date.
4. Delete anything older than N days.
5. **Notify only on failure** — a daily "backup fine" message trains people
   to ignore it, which is the opposite of a backup alert.

**Credentials** the storage; whatever exports.

**What usually goes wrong** The delete step running when the upload failed,
leaving neither the new backup nor the old one. Make the delete conditional
on the upload node's success, explicitly.

---

## 6. Watch a page and say when it changes

**Trigger** Schedule, hourly or so.

**Steps**
1. Schedule trigger.
2. HTTP Request for the page.
3. Extract the part that matters with a CSS selector — never hash the whole
   page, which changes on every ad and every timestamp.
4. Compare against the last value in n8n's static data.
5. If different, notify with both values, then store the new one.

**Credentials** usually none.

**What usually goes wrong** Hashing the whole page and getting an alert every
hour. Also: be a good citizen — hourly at most, and honour the site's terms.

---

## 7. Turn a form into a task, a message and a row

**Trigger** Webhook (n8n's own form trigger, or a Tally/Typeform hook).

**Steps**
1. Webhook trigger.
2. Validate: reject anything without the required fields, and respond 400.
3. Fan out — create the task, post the message, append the row.
4. Respond to the webhook with a real 200 and a body.

**Credentials** the task system; the chat; the sheet.

**What usually goes wrong** The production URL. n8n gives a workflow a *test*
webhook URL that only listens while the editor is open, and a *production*
one that only works once the workflow is active. A form pointed at the test
URL works while somebody is watching and silently stops when they close the
tab. Say which URL to use, every time.

---

## 8. Move a file when it lands

**Trigger** Drive/Dropbox trigger on a watched folder.

**Steps**
1. The file trigger.
2. Branch on type: images one way, PDFs another, everything else to a review
   folder rather than nowhere.
3. Rename to a convention — `YYYY-MM-DD-slug.ext`.
4. Move, and log the move.

**Credentials** the storage.

**What usually goes wrong** The workflow moving files it created, and
re-triggering itself. Watch one folder and write to a different one — never
the same one.

---

## 9. Take a message and put it somewhere useful

**Why** "Save this for me" is the most common ad-hoc request there is.

**Trigger** Telegram/Slack/WhatsApp message trigger, or an email to a
dedicated address.

**Steps**
1. Message trigger.
2. Classify: a link, a note, a task, an image.
3. Route to the right destination — a reading list, a notes database, a task
   list, a folder.
4. React or reply so the sender knows it landed.

**Credentials** the messenger; each destination.

**What usually goes wrong** No acknowledgement. A silent workflow is
indistinguishable from a broken one, and the sender re-sends. Always reply,
even with an emoji.

---

## 10. Summarise a long thing on demand

**Trigger** Webhook, or a message with a URL in it.

**Steps**
1. The trigger, carrying a URL or a document.
2. Fetch and extract text.
3. Chunk it, if it is long. Do not send fifty pages to a local model in one
   request.
4. Summarise — pointed at the household's own model, not a cloud one.
5. Return the summary.

**Credentials** the model endpoint, if it needs one.

**What usually goes wrong** Timeouts. A local model summarising fifty pages
takes minutes, and the webhook caller has given up. Respond immediately with
"working on it" and send the result separately.

---

## 11. Reconcile two lists

**Why** Boring, and the thing spreadsheets are worst at.

**Trigger** Schedule, or on demand.

**Steps**
1. Fetch both lists.
2. Key them on something stable — an id, not a name.
3. Three outputs: in A only, in B only, in both but differing.
4. Report the three counts and the rows, not just "there are differences".

**Credentials** both sources.

**What usually goes wrong** Keying on a name. "ACME Ltd" and "Acme Limited"
are one supplier and two keys, and the report is noise. Normalise, or key on
something that is actually an identifier.

---

## 12. A workflow that another workflow calls

**Why** Once there are three workflows that all send a notification the same
way, that is one sub-workflow.

**Trigger** Execute Workflow trigger.

**Steps**
1. Execute Workflow trigger, with a declared input shape.
2. The shared thing — format and send, or fetch and normalise.
3. Return something the caller can use.

**Credentials** whatever the shared step needs, once, here.

**What usually goes wrong** No declared input shape, so every caller passes a
slightly different object and the sub-workflow grows conditionals for each.
Declare the fields, and make the callers conform.

---

## Two rules that apply to all of them

**Credentials are attached by a person, in n8n.** Jarvis writes the workflow
with the credential block as normal and the block is stripped before it is
sent, because any id a model writes is a guess. Report what was asked for and
who has to attach it.

**Nothing is switched on by Jarvis.** Every workflow above arrives inactive.
That is not a limitation to apologise for — a workflow nobody has read has
not run, and most of these cannot work until credentials are attached anyway.
