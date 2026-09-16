# trueffelsau

trueffelsau finds product ideas that have a high chance of success. It runs once
a week and needs almost no manual work.

The name is Austrian dialect for "truffle pig": an animal whose job is to find
something valuable buried in dirt.

## What it does

1. It collects public signals. A signal is a price increase, a forced login, a
   license change, a shutdown, an acquisition, an API lockdown, a business for
   sale, or a repeated complaint.
2. It classifies each signal with an LLM.
3. It scores each candidate against a fixed filter.
4. It writes a digest every Monday.

You read the digest and decide. The tool records your decision and asks again
six months later. The filter gets better because of that answer.

Read [`CLAUDE.md`](CLAUDE.md) for the full specification. It defines the filter,
the data model, the sources, and the scoring.

## What it does not do

- It does not build products. It finds candidates.
- It does not predict revenue. It reports evidence.
- It does not collect personal data.
- It does not scrape sites that forbid it.

## Status

Early. The specification is complete. The code is not.

## Install

You need Python 3.12 or newer.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

## Public and private

The code, the schemas, and the specification are public under AGPL-3.0.

Your own data stays out of this repository. trueffelsau keeps it in a runtime
home directory instead:

```
~/.local/share/trueffelsau/
```

Set `TRUEFFELSAU_HOME` to move that directory. It holds your weights, your
watchlist, the database, the digests, and your API keys. None of it is
committed.

## Contributing

`main` is protected. Every change arrives as a pull request, and the checks must
pass before it can merge.
