# Test image provenance

Face fixtures are real photographs of real people, committed rather than
generated, because a generated face is useless for testing a face model: it
scores a drawing as confidently as a photograph, so a test on synthetic fixtures
would pass while proving nothing.

All seven are public domain or CC BY, retrieved from Wikimedia Commons. Each is
pinned by SHA-256 in `tests/test_face_compare.py`, which refuses to run if a file
changes.

| File | Subject | Source | Licence |
|---|---|---|---|
| `turing_a.jpg` | Alan Turing, 1951 | `File:Alan Turing (1951).jpg` | Public domain |
| `turing_b.jpg` | Alan Turing, age 16 | `File:Alan Turing at age 16.jpg` | Public domain |
| `obama_1.jpg` | Barack Obama | `File:Official portrait of Barack Obama.jpg` | CC BY 3.0 |
| `obama_2.jpg` | Barack Obama | `File:President Barack Obama.jpg` | Public domain |
| `trump_1.jpg` | Donald Trump | `File:Donald Trump official portrait.jpg` | Public domain |
| `franklin_a.jpg` | Benjamin Franklin, 1767 | `File:Benjamin Franklin 1767.jpg` | Public domain |
| `lovelace_a.jpg` | Ada Lovelace, 1843 | `File:Ada Lovelace.jpg` | Public domain |
| `bus.jpg` | street scene | `https://ultralytics.com/images/bus.jpg` | ultralytics (canonical YOLO test image) |

## Why two images per subject

`turing_a`/`turing_b` and `obama_1`/`obama_2` are two *different* photographs of
the same person. Comparing one file against itself would prove nothing — cosine
similarity is 1.0 for any vector with itself — so the same-identity case has to
come from two separate captures.

## Licence note on the licences themselves

`obama_1.jpg` is CC BY 3.0, which requires attribution. The source is recorded
above and in the test module docstring; the Openverse/Commons attribution for it
is the White House official portrait. If these fixtures are ever moved into a
shipped product rather than a test suite, that attribution obligation travels with
the file.

The alternative — InsightFace's ArcFace weights, which most face-recognition
projects use — is licensed for **non-commercial research only**, so it cannot be
shipped in a forensic product. See `CHECKLIST.md` P6.B1.