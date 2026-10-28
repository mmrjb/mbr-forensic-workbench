# Investigation report \- DEMO \- Synthetic LAB\-WIN\-01

Generated 2026-10-28T07:12:41.278076Z by MBR Forensic Workbench 0.1.0.

> **DEMO CASE** - synthetic data.

## Case

- Case ID: `case-79fbacccf192`
- Examiner: Demo examiner
- Display timezone: Asia/Tehran

## Scope

SYNTHETIC DEMO\. Scope: synthetic collection of LAB\-WIN\-01 \(Prefetch, event logs, $MFT\)\. Not examined: memory, network logs, other hosts\.

## Findings

### Unfamiliar executable run from a user Temp folder \(DEMO\)

Status: open; analyst-assigned severity: high; version 4

Synthetic example\. Prefetch records UPDATER\_TMP\.EXE running at 09:16:03 UTC; the $MFT shows the file created in the user's Temp folder seconds earlier, and a service pointing to it was installed\. Presence in Amcache/ShimCache alone would not have shown execution\.
متن فارسی برای آزمایش نمایش راست‌به‌چپ\.

- event 19: $STANDARD\_INFORMATION created at `2026-09-14 09:15:59.8811234 +00:00` (original `2026-09-14 09:15:59.8811234`, tool_utc_convention) from `ds4:rec1`
- record `ds4:rec1` (mftecmd SYNTHETIC-DEMO, job `None`)
- record `ds3:rec4` (evtxecmd SYNTHETIC-DEMO, job `None`)

## Processing history

| Job | Tool | Version | State | Parser | Import |
|---|---|---|---|---|---|

## Limitations

- Processing success means a parser ran and its output was imported; it does not mean the host is clean or that no other activity occurred.
- An empty result or a filter with no matches only describes the processed artifacts and scope listed here.
- Presence artifacts (Amcache, ShimCache, RecentFileCache) are not by themselves proof of execution; file system and ShellBag timestamps are not by themselves proof of user access.
- Hashes and logs support reproducibility; they do not by themselves establish legal admissibility or a complete acquisition history. The case database is not cryptographically tamper-proof.
