---
name: note-taking
description: When something belongs in a note rather than in memory or in the conversation, and how to write one worth reading later.
allowed-tools: [note_create, note_append, note_search]
metadata:
  author: Jarvis
  permissions: [read_state, memory_read, memory_write]
version: "1"
---

# Notes

## Which of the three

Jarvis has three places to put something, and using the wrong one is how a
house assistant becomes annoying:

* **The conversation.** Anything that stops mattering when the conversation
  ends. Most things.
* **Memory** (`remember`). One durable fact about the household — "the boiler
  service is due in March", "Sam is allergic to shellfish". A sentence, kept
  forever, read back in every conversation. Only when the user has asked for
  it in their own words.
* **A note.** Anything longer than a sentence, anything with structure, and
  anything they will want to open and read: a shopping list, a research
  report, the model numbers off the back of the boiler.

A note is not a slower memory. Notes are read when somebody asks for them;
memory is read every time.

## Writing one

Search first (`note_search`). A second note called "Boiler" is worse than a
messy first one, because now neither is complete.

The title is what somebody will search six months from now. "Boiler" beats
"Notes from Tuesday". Put the date in the body, never in the title — a title
with a date in it is a note nobody updates, they just make another.

Append (`note_append`) rather than rewrite. What was there was true when it was
written, and a note that quietly loses a line is worse than one that grows.

## What does not go in a note

Passwords, keys, tokens, and anything a person read out to you as a secret. If
somebody asks you to note one down, say plainly that you do not keep secrets in
notes, and that Jarvis has a secrets store for exactly that.
