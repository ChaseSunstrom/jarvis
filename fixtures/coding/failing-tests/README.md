# failing-tests — a small project whose tests do not pass

The fixture `evals/coding_eval.py` drives Jarvis Code against. It is
deliberately tiny and deliberately *specific*: three failures, each of a
different kind, so a job that passes has demonstrably read the tests rather
than reverted a file or deleted a case.

| Failure | What it takes to fix |
|---|---|
| `add` returns the wrong thing for negatives | reading a branch and correcting it |
| `slugify` keeps punctuation | writing a small amount of new code |
| `Basket.total` ignores quantity | understanding two functions at once |

The tests themselves are the specification and must not be edited — the eval
fails the job if the test files change, because "make the tests pass" and
"delete the tests" are different instructions and only one of them is the job.

The check is `python -m unittest discover -s . -p "test_*.py"`, standard
library only and on purpose: the job runs it inside a container with **no
network**, so a suite that needed `pip install` first could never go green.

Not a git repository as it ships: the eval copies it somewhere temporary and
runs `git init`, so a failed job can never leave anything behind here.
