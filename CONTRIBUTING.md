# Contributing to TurnitOut

Thanks for your interest in improving TurnitOut — a free, open-source similarity and
AI-writing checker for educators. Contributions are welcome **with maintainer approval**:
open an issue first, describe the value, and get a thumbs-up before writing lots of code.
This keeps the project coherent and avoids duplicated effort.

## Governance (short version)

- The maintainer (**@alfredshingai**) approves what gets merged.
- Significant features (new engines, new providers, schema changes, UI overhauls) require
  an approved issue **before** a pull request.
- Small, obvious fixes (typos, bug fixes with a reproducing test, docs) can go straight to
  a PR and will be reviewed quickly.
- All PRs should pass the test suite: `python -m unittest discover -s tests` and
  `python tests/smoke.py`.

## What's wanted (high-value ideas)

- **Detection accuracy**: calibration harness, ensemble scoring with agreement confidence,
  richer stylometry, better bypasser fingerprints.
- **Engine scale**: precomputed k-gram index so scans stay fast on large corpora.
- **Turnitin-parity features**: quote/bibliography exclusion, report annotations,
  PDF/CSV exports.
- **Localization**: the UI is English-only today; more languages welcome.
- **Documentation**: deployment guides for more platforms, screenshots, tutorials.

## What's not wanted

- Anything that evades the checks this app performs (humanizers, bypass tools). The
  bypasser *fingerprint* exists to expose spun text, not to perfect it.
- Features that send user text to third parties without an explicit, documented opt-in.
- Dependency-heavy rewrites — the stdlib-only Python core is a deliberate design choice.

## Ground rules

- Keep the Python standard library as the only hard dependency; optional integrations
  (LM Studio, GPTZero) must fail soft when absent.
- Every detection change needs tests, including false-positive-conscious cases
  (non-native writing patterns, formal register).
- Detection is probabilistic: never add UI that presents a score as proof of misconduct.

## Reporting issues

Include: OS, Python version, steps to reproduce, and (for UI issues) the browser.
For security issues, please email the maintainer directly rather than opening a public issue.
