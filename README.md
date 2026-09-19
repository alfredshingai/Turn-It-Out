# 🔍 TurnitOut

Free, anonymous **similarity + AI-writing checks** for any text — essays, articles, reports, letters, anything written. No account, no signup, no email. Paste text or upload a file, get a report at a private link.

> **Free & open source.** TurnitOut is and will remain a free, open-source platform.
> No subscriptions, no per-document fees, no vendor lock-in. If it saves you money
> compared to commercial suites, consider [contributing](CONTRIBUTING.md) code, docs,
> translations, or test data instead.
>
> **🤝 Call for contributions & collaborations.** Detection engines, calibration data,
> translations, deployment guides — if you can add value, it belongs here. Contribution
> model: propose first, merge with maintainer approval. See [CONTRIBUTING.md](CONTRIBUTING.md).

**How it works** — [Quick start](#run-it) · [Deployment](DEPLOY.md) · [Contributing](CONTRIBUTING.md)

## How it works

1. **Paste or upload** — any text (20–30,000 words) or a .txt / .md / .docx / .pdf file.
2. **The engine checks it** against:
   - the **shared anonymized corpus** of every past scan (sources shown only as "Document #N" — never filenames or text),
   - the **live web** via distinctive-phrase search,
   - **AI-writing patterns** (perplexity under a real language model when LM Studio is running, plus burstiness, style signals, and a bypasser fingerprint).
3. **Read the report** at a private link — similarity % with exact passage highlighting, AI-writing likelihood with flagged sentences. The link is the only key: unguessable, shareable, expiring only when you delete the data.

**No accounts.** A scan creates a document addressed by a 128-bit random token. Whoever holds the token can read that report; nobody can browse anyone else's. Optional per-scan checkbox: include your text in the shared corpus (on by default — every scan makes the tool smarter for everyone) or keep it fully private. Either way it is still checked *against* the corpus.

## Run it

```
python run.py            # http://127.0.0.1:8333
python run.py --lan      # reachable from other devices on your network
python run.py 9000       # custom port
```

Zero dependencies — the Python standard library is the whole stack. On first run a demo
document is seeded and its link printed in the console, so you can see a finished report
immediately. Delete `data/turnitout.db` to reset. Set `TURNITOUT_DEMO=0` to skip seeding.

Deployment options (LAN, Docker + automatic HTTPS, Render free tier): see [DEPLOY.md](DEPLOY.md).

## The similarity engine

Turnitin-style fingerprinting:

1. Text is normalized (case, punctuation, quotes) and tokenized to words.
2. 6-word **fingerprints** (k-grams) are matched against every corpus source and the web.
3. Seed matches are greedily extended into maximal matching spans.
4. Tiny, generic overlaps (mostly stopwords, <5 words) are filtered out.
5. **Score** = matched words ÷ document words, with per-source percentages and highlighted passages.

## AI-writing detection

Modeled on Turnitin's published methodology:

- **Qualifying text only** — prose sentences in long-form writing; bullets, tables, code,
  headings, citations and fragments excluded.
- **300-word prose minimum** for reliable analysis; shorter text returns `insufficient_text`.
- **The asterisk rule** — scores between 1–19% display as `*%` because that range has the
  highest false-positive risk (same policy Turnitin applies).
- **Sentence-level highlighting** of passages scoring ≥50%.
- **Bypasser fingerprinting** — text showing the signature of AI-generation followed by an
  AI-paraphrasing tool is classified separately as *"Likely AI-generated + AI-paraphrased"*.

### Provider tiers (priority order, each fails softly to the next)

1. **GPTZero API** — model-grade sentence-level detection when a key is configured:

   ```
   # .env
   GPTZERO_API_KEY=your-key-here
   ```

   Get a key at gptzero.me (paid API). Sends text to a third party.

2. **LM Studio local model scoring** — *real* model-based detection, 100% local and free.
   Install [LM Studio](https://lmstudio.ai), open the **Developer** tab, load a small model,
   click **Start Server** — TurnitOut auto-detects it and scores your text's **perplexity**
   (AI text is highly predictable). With two models loaded it adds a **cross-model ratio**
   (Binoculars-inspired, ICLR 2024). No data leaves the machine.

3. **Local heuristics** — the always-available fallback: burstiness (sentence-length
   variance), contraction/hedge/first-person rates, vocabulary predictability, and the
   bypasser fingerprint. Transparent and explainable, but the weakest signal.

### Known limitations (all detectors, including Turnitin's)

- Human writing **can** be flagged (false positives): formal, repetitive, highly-structured,
  or non-native-English writing resembles AI patterns.
- AI writing **can** evade detection (false negatives), especially after light editing.
- Short submissions are unreliable — hence the 300-word minimum and the `*%` band.
- A score is a **conversation starter, not a verdict**: consider drafts, version history,
  and the author's ability to explain the work before drawing conclusions.

### What this app deliberately does not include

No "AI humanizer" / bypass mode. The app's purpose is integrity checking; a tool whose
purpose is evading such checks has no place in it. The bypasser *fingerprint* exists so
flagged paraphrasing is visible, not to help it succeed.

## Notes

- Data lives in `data/turnitout.db` (SQLite, WAL mode). It stays on your machine unless
  you deploy it; the GPTZero tier is the only feature that sends text anywhere.
- Web checking needs internet; scans always succeed without it.
- PDF extraction is best-effort (uncompressed/Flate streams). Scanned image PDFs are
  rejected with a clear error.
- Rate limit: 10 scans/hour per IP — free for humans, hostile to scrapers.
