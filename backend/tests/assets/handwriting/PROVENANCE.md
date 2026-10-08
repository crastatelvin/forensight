# Handwriting fixtures — `handwriting-match`

Four greyscale page images in `backend/tests/assets/handwriting/`. **Public domain.**

## Provenance

*Charlotte Baker's 1914 diary*, [item `csdhi_000081`](https://archive.org/details/csdhi_000081)
on [archive.org], digitised by the [Center for Digital History](https://unh.edu/cdh),
University of New Hampshire.

Licence: **CC Public Domain Mark 1.0** —
`http://creativecommons.org/publicdomain/mark/1.0/`

| Fixture | PDF page | Dated |
|---|---|---|
| `p100.png` | 100 | Tuesday, 28 March 1914 |
| `p160.png` | 160 | Sunday, 26 July 1914 |
| `p220.png` | 220 | Monday, 23 November 1914 |
| `p260.png` | 260 | Cash Account — March |

## How these were located

The 1914 volume is 288 pages and its diary body is **interleaved with printed
ephemera** — tide tables, principal-cities lists, stock prices. A contact sheet of all
288 pages at 12 dpi showed the structure directly: printed tables for roughly pages
1–64, dense handwriting for roughly pages 65–272, printed material again after.

**An ink-density filter does not find the handwriting pages.** Seventeen sampled pages
in the diary range all scored 0.13–0.19 ink, printed ephemera included, so density is
useless here. The pages above were chosen by eye from the contact sheet and each
verified at full resolution before use. *Choosing them by density would have put
printed tables in a handwriting fixture set, which measures nothing.*

## Regenerating

```bash
pdftoppm -f 100 -l 100 -r 200 -gray -png csdhi_000081_access.pdf hp100
```

`-r 200` (200 dpi) is what the fixtures were rendered at. Resolution materially changes
the keypoint count, and the measured thresholds depend on the resolution these were
taken at.

## What these fixtures do and do not establish

**They do support one thing clearly.** ORB matching separates the same page under a
re-capture from a different page of the same diary by roughly thirty-fold — same page
0.380–0.696, different page 0.007–0.013.

**They do not establish anything about authorship**, and this is the important limit:
all four pages are one writer, so "same writer, different page" is the negative class.
That makes this a **document-matching** test, not a writer-identification one. Two pages
sharing a hand says nothing about who wrote either — an author keeps one hand, a
form-filler imitates one, a forger copies one.

**The re-captures are mild**: recompression, small rotation, rescaling. Page curvature,
perspective, raking light, a different pen and a different scanner are all untested,
and all plausibly defeat the comparison.