# Tasks: the record, and the thing that runs them

Two files, and the split is the whole design.

`jarvis/tasks.py` is the **record**: what is happening, how far it has got, what
it called, what it printed. It runs nothing, and says so.

`jarvis/taskengine.py` is the **engine**: a bounded queue, a small pool of
workers, retries with backoff, and work that survives a restart.

They were one thing short of useful for a while. `run_background_task` minted an
id, fired an event nobody listened to, and told the model to say the result
would arrive later; Jarvis Code and research each grew their own unbounded
`ensure_future`. Three jobs at once meant three conversations against one model
server with one KV cache, and the symptom was not an error — it was everything
becoming four times slower at once, which reads as "Jarvis is broken today".

## Submitting work

```python
engine = jarvis.taskengine
task = await jarvis.tasks.async_add("Read twelve pages", kind="research")
engine.submit(task.id, worker, kind="research", retries=1, idempotent=True)
```

A worker is `async def worker(task_id: str) -> None`. It reports through the
registry, calls `jarvis.tasks.raise_if_cancelled(task_id)` wherever stopping is
safe, and raises to fail. The engine owns *when* work runs and *how often it is
retried*, and knows nothing about what the work is.

## The concurrency limit

`llm.max_concurrent` (default 2) is the number of jobs that may run at once,
because every one of them eventually talks to the same model server. The queue
is what makes exceeding it impossible rather than merely discouraged; a
submission past `MAX_QUEUED` is refused with a message a person can read.

Not everything queues. A scheduled `notify` fires on the spot: a reminder
waiting behind a twenty-minute coding job is a reminder that arrives after the
thing it was reminding you about. Only `research` and `code` — the kinds that
hold a model conversation — go through the queue.

## Retries

`retries=n` gives a failure `n` more attempts, with backoff of 2s, 8s, 32s,
capped and jittered so three jobs failing against the same dead model server do
not retry in lockstep for ever. A `TaskCancelled` is not a failure and is never
retried: somebody asked it to stop and it did.

## What survives a restart

The queue is persisted in the same file as the task list, written atomically,
because a queue saved separately can disagree with the list it refers to and the
disagreement only shows up after a crash.

* **queued** work is still queued. `Task.restored` used to error it, which was
  right while nothing could pick it up.
* **running** work is errored — the thing driving it is gone — unless the
  submission said `idempotent=True`. Re-running half of something that already
  half-happened is a worse failure than reporting that it stopped, and only the
  worker knows which it is.
* a **queued** task the engine's queue does not mention is failed by
  `TaskEngine.load`, which is the only place that can tell.

## Retrying by hand

`jarvis/tasks/retry` (WS) puts a finished task back on the queue — the button
somebody presses after fixing whatever broke. It refuses work whose kind this
server cannot rebuild, rather than being a button that does nothing.
