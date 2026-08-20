---
name: coding-jobs
description: Use when the user asks for a program, a script, a game, a website, or a change to code — anything that means writing or editing source files. Covers picking or creating a repository, what a coding job can and cannot do, and how to report one honestly.
license: Apache-2.0
---

# Writing code

`start_coding_job` runs a coding agent against one repository, on a branch of
its own. It is a background job: it takes minutes, not seconds.

## Getting a repository first

Call `list_code_repositories`. If nothing there fits what the user asked for,
call `create_repository` — asked for a Snake game, make `snake`, do not
explain that there is nowhere to put it.

The repository must exist before the job starts. A job cannot create its own.

## Starting the job

Say what to change in full. The job runs alone and cannot ask you a follow-up
question, so "add tests" is three minutes of guessing and "add pytest tests
for the parser in src/parse.py, covering the error cases" is a useful job.

`start_coding_job` needs approval. When you get `approval_required`, that is
the honest end of your turn — say a human has to approve it, and stop.

## While it runs and after

It reports through the ordinary task machinery, so the user can watch it. When
it finishes there is a branch, a diff and the results of whatever checks the
repository declares.

Report what actually happened: the branch name, what changed, and whether the
checks passed. If checks did not run, say so — a repository that can be
written to and has no sandbox does not get to run its own checks, and that is
a configuration fact worth relaying rather than hiding.

**Never say the work is done before the job has finished.** The job's own
result is the only thing that knows.

## What it cannot do

- It never commits to the branch the user is on.
- It refuses to start on a dirty tree.
- It cannot push anywhere unless the user asks and approves separately.
