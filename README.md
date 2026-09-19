# 🔍 TurnitOut

A Turnitin-style **similarity + AI-writing checker** for classes and assignments — built entirely on the **Python standard library** (no dependencies to install).

> **Free & open source.** TurnitOut is and will remain a free, open-source platform for
> educators and students. No subscriptions, no per-document fees, no vendor lock-in.
> If it saves you money compared to commercial suites, consider [contributing](CONTRIBUTING.md)
> code, docs, translations, or test data instead.
>
> **🤝 Call for contributions & collaborations.** Detection engines, calibration data,
> translations, deployment guides — if you can add value, it belongs here. Contribution
> model: propose first, merge with maintainer approval. See [CONTRIBUTING.md](CONTRIBUTING.md).

**How it works** — [Quick start](#run-it) · [Deployment](DEPLOY.md) · [Contributing](CONTRIBUTING.md)

## Features

- **Instructor & student accounts** with cookie sessions (scrypt-hashed passwords)
- **Classes** with join codes, rosters, and assignments
- **Document upload** (.txt, .md, .docx, .pdf — text-based) with automatic scanning
- **Turnitin-style similarity reports**: overall %, per-source %, and exact passage highlighting in the document
- **Database corpus**: every submission is checked against all other submissions in the app
- **Web check**: distinctive phrases are searched on the web (DuckDuckGo); matching snippets become web sources in the report. Works offline too — web sources are simply absent.
- **AI-writing detection**: every scan also estimates the likelihood the text was machine-generated, with sentence-level highlighting on the report's "AI writing" tab
- **Rescan** any submission on demand

## AI-writing detection

Modeled on how Turnitin's AI detector works (per their published documentation):

- **Qualifying text only** — analysis runs on prose sentences in long-form writing. Bullets, tables, code, headings, citations and short fragments are excluded, exactly like Turnitin's "qualifying text" rule.
- **300-word prose minimum** — Turnitin's threshold for reliable analysis; shorter documents report `insufficient_text` instead of a guess.
- **The asterisk rule** — scores between 1–19% display as `*%` ("no reliable AI signal") because that range has the highest false-positive risk. Same policy Turnitin applies.
- **Sentence-level highlighting** — qualifying sentences scoring ≥50% are highlighted on the AI tab.
- **Bypasser fingerprinting** — text showing the signature of AI-generation followed by an AI-paraphrasing tool (synonym-swapped, connective-dense, uniformly rhythmic) is classified separately as *"Likely AI-generated + AI-paraphrased"*.

### Provider tiers (priority order, each fails softly to the next)

1. **GPTZero API** — model-grade sentence-level detection when a key is configured:

   ```
   # .env
   GPTZERO_API_KEY=your-key-here
   ```

   Get a key at gptzero.me (paid API; per-document pricing). Sends text to a third party.

2. **LM Studio local model scoring** — *real* model-based detection, 100% local and free.
   If you have [LM Studio](https://lmstudio.ai) installed: open the **Developer** tab,
   load a small instruct model (e.g. Llama 3.2 3B or Qwen 2.5 3B), click **Start Server**,
   and TurnitOut auto-detects it. Scoring runs the text through the model and measures
   **perplexity** (how predictable the text is — AI text is highly predictable); with two
   models loaded it adds a **cross-model ratio** (Binoculars-inspired, ICLR 2024), which is
   state-of-the-art zero-shot detection. Paragraph-level highlighting included. No data
   ever leaves the machine. Tune with `LMSTUDIO_URL`, `LMSTUDIO_EXPERT`, `LMSTUDIO_BASE`.

3. **Local heuristics** — the always-available fallback: burstiness (sentence-length
   variance), contraction/hedge/first-person rates, vocabulary predictability, and the
   bypasser fingerprint. Transparent and explainable, but the weakest signal of the three.

### Known limitations (all detectors, including Turnitin's)

- Human writing **can** be flagged (false positives): formal, repetitive, highly-structured, or non-native-English writing resembles AI patterns.
- AI writing **can** evade detection (false negatives), especially after light editing.
- Short submissions are unreliable — hence the 300-word minimum and the `*%` band.
- A score is a **conversation starter, not a verdict**: review drafts, version history, notes, and the student's ability to explain their work before drawing conclusions. Turnitin says the same about its own reports.

### What this app deliberately does not include

No "AI humanizer" / bypass mode. The app's purpose is integrity checking; a tool whose purpose is evading such checks has no place in it. The bypasser *fingerprint* exists so flagged paraphrasing is visible to instructors, not to help it succeed.

Privacy note: using GPTZero sends submission text to a third party. If that's a concern for your institution, run in heuristic mode — it keeps all data local.

## How the similarity engine works

1. Text is normalized (case, punctuation, quotes) and tokenized to words.
2. 6-word **fingerprints** (k-grams) of each document are matched against every source.
3. Seed matches are greedily extended into maximal matching spans.
4. Tiny, generic overlaps (mostly stopwords, <5 words) are filtered out.
5. **Score** = matched words ÷ document words. Report highlights show exactly which sentences matched which source.

## Run it

```
python run.py            # http://127.0.0.1:8333
python run.py 9000       # custom port
```

On first run the app seeds demo data:

| Role | Email | Password |
|---|---|---|
| Instructor | `instructor@demo.edu` | `instructor123` |
| Student | `student@demo.edu` | `student123` |

The student account already has a submission (Essay 1) that copies from the built-in reference corpus — open its report to see highlighted matches immediately.

## Demo flow

1. Sign in as the **student**, open *ENG 101 → Essay 1*, submit a .docx/.txt and watch the scan run.
2. Sign in as the **instructor** to see all submissions and their similarity bars.
3. Create a class as an instructor, share the join code, join as a student.

## Notes

- Data lives in `data/turnitout.db` (SQLite, WAL mode). Delete the file to re-seed.
- Web checking needs internet; scans always succeed without it.
- PDF extraction is best-effort (uncompressed/Flate streams). Scanned image PDFs are rejected with a clear error.
