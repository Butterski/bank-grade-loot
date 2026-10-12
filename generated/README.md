# Asking a model to write the trade

Asked what the most common mistake on the servers he dupes is, a well-known duper answered: "they use ai to create
their own plugins that allow us to exploit very easily".

This folder is about the other half of that sentence: how to ask, so that what comes back holds. It has the prompts,
the answers of four models, and a way to try the model you use.

```bash
python -m loot generated
python -m loot generated fable-plain 02
```

## What to do

1. **Give the model the documentation.** Both prompts here carry the calls of the database, what they return, and
   what happens when two requests meet: who waits, and what a plain read can miss.
2. **Say what happens on a live server.** [`prompt-with-requirements.md`](prompt-with-requirements.md) adds four
   sentences to the short request: two buyers at the same moment, the same request twice, a process that stops
   between two calls, and one player in two purchases at once. They are the checks of this repository in plain
   words.
3. **Run the checks on what comes back.** A prompt raises the odds. Only a check tells you what you got.

For another operation, say the same four things about it: two requests for the same thing, the same request twice,
a stop halfway, one account in two operations. Here they were tried on the trade only.

## The two prompts

| Prompt | What is in it | Handlers that pass every scenario |
|---|---|---|
| [`prompt-plain.md`](prompt-plain.md) | A short request and the documentation of the database | 3 of 4 |
| [`prompt-with-requirements.md`](prompt-with-requirements.md) | The same, the four sentences, and a table for operation IDs | 4 of 4 |

The one handler that does not pass tells a repeated request "you already own this item" about a purchase that went
through. Its author, a model, says so in a comment: nothing in the short request gave it a place to keep a result.
With either prompt, no handler duplicated the sword, let a balance go below zero or left half a trade behind.

## How the samples were made

- **When:** 12 October 2026.
- **The models:** `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5` and `claude-fable-5-1`, as each model
  named itself. One answer per model and prompt: eight handlers.
- **How they were asked:** each answer came from a separate Claude Code subagent. Its whole instruction was to read
  the prompt file, to make no other tool call, and to answer from that file alone. None of them saw the checks or
  the other designs.
- **What is stored:** [`raw/`](raw) holds each reply as it arrived. [`samples/`](samples) holds the first Python code
  block of each reply, cut out by [`extract.py`](extract.py). Nothing was fixed, renamed or reformatted.

## Two decisions that affect the result

- **One more table.** Two handlers written from the short request say in a comment that they need a table
  `purchases` keyed by `op_id`. They get it, empty ([`extra-tables.json`](extra-tables.json)). Without it they fail
  on the first call with a missing table, and adding it is what their author would do next.
- **"The same answer" means the same yes or no.** A repeated request passes when it is told that the purchase went
  through. The wording may differ.

## What this does not show

- It is one answer per model and prompt. That is an anecdote, not a benchmark, and a second run can differ.
- It is four models from one vendor, asked through a coding assistant that was opened in the folder of this talk.
  A clean API call would be a better experiment.
- Both prompts carried the documentation. Nothing here says what a model writes without it.
- The tables already had the right shape: one row per item, one owner column.
- The checks are the five scenarios in the table. Passing them is not a proof that a handler is correct.

## Try the model you use

1. Paste one of the two prompts into the model.
2. Save the whole reply as `raw/<name>-plain.md` or `raw/<name>-with-requirements.md`. A last line such as
   `MODEL: some-model-id` sets the name in the table.
3. Run `python generated/extract.py`, then `python -m loot generated`.
