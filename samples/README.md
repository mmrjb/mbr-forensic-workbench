# Samples (synthetic)

Everything in this folder is **synthetic demonstration data**, created by `python scripts/make_sample.py` from `src/mbrwb/samples.py`:

- `demo_case.zip` - a complete DEMO case folder (case.json, case.sqlite, results.sqlite, evidence manifests, a synthetic evidence tree containing only file signatures, and synthetic CSV outputs). Extract it and open the folder with **Ctrl+O**. It is marked `is_demo` and shows a DEMO badge.
- `example_report.html` / `example_report.md` - reports generated from that case (display timezone Asia/Tehran; UTC always included).
- `example_report_redacted.html` - the same report with host, user and examiner redaction (the case data is unchanged).
- `example_case_manifest.json` - machine-readable case/job manifest.

The synthetic CSVs are shaped like PECmd / EvtxECmd / MFTECmd output and were imported as *external outputs* declared "SYNTHETIC DEMO"; no parser job was fabricated, and they say nothing about the real tools. Host `LAB-WIN-01`, user names (including the Persian test string `جعبه‌تست`), file names and timestamps are invented.
