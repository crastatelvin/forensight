# Speaker fixtures — `speaker-compare`

Eight 8-second WAV excerpts used by `tests/test_golden_speaker.py`. **All public
domain.**

## Provenance

Source: [LibriVox](https://librivox.org), via
[archive.org](https://archive.org). LibriVox recordings are contributed by volunteers
who dedicate their work to the **public domain**, and the books they read are themselves
out of copyright.

| Fixture | Reader | Book |
|---|---|---|
| `kara_a.wav` | Kara Shallenberg | *Letters of Two Brides* (Balzac) |
| `kara_b.wav` | Kara Shallenberg | *The Secret Garden* (Burnett) |
| `kara_c.wav` | Kara Shallenberg | *The Odyssey* (trans. Butler) |
| `lyons_a.wav` | Cynthia Lyons | *Bleak House* (Dickens) |
| `lyons_b.wav` | Cynthia Lyons | *Beowulf* (trans. Gummere) |
| `lyons_c.wav` | Cynthia Lyons | *Mother Goose in Prose* (Baum) |
| `sirois_a.wav` | Michael Sirois | *Penguin Island* (France) |
| `sirois_b.wav` | Michael Sirois | *Narrative of Arthur Gordon Pym* (Poe) |

Readers appear under their LibriVox display names, which include birth/death years where
LibriVox records them. Recordings were made across many years with different equipment,
which is why `kara_a`/`kara_b`/`kara_c` are genuinely different recording sessions rather
than three takes in one room.

## How these were made

Each excerpt is the **middle 8 seconds** of the full decode, at 16 kHz mono, written as
16-bit PCM WAV. Regenerate with:

```bash
ffmpeg -v error -i <source.mp3> -ac 1 -ar 16000 -f f32le -acodec pcm_f32le - \
  | python3 -c 'import sys,wave,numpy as np; …'   # slice the middle, write WAV
```

**Decode the whole file and slice afterwards.** Fast-seek (`-ss` *before* `-i`) into a
large MP3 with no seek index returns **0 bytes with exit code 0** — a silent failure that
is indistinguishable from a decode error, and it cost real time to find.

The middle, not the start: a LibriVox chapter opens with near-silence and a spoken
credit, and an embedding over that measures the recording rather than the reader.

## What these fixtures do and do not establish

**They do not establish that speaker verification works.** Measured over these eight
readings, same-reader cosine similarity runs **0.665–0.906** while different-reader
similarity reaches **0.874** — the ranges overlap, and no threshold separates them.

**`kara_b.wav` is retained as a known failure, and the reason is the fixtures rather
than the technique.** LibriVox recordings are *dramatic readings*: one narrator performs
several characters. *The Secret Garden* is read in character voices, so `kara_b` is
further from its own narrator (0.665) than from a different reader (0.874) — and no
speaker embedding should match it. Excluding it separates the rest by **0.0004**, which
is a coin flip rather than a threshold.

It is kept rather than dropped because removing it after seeing the result would leave a
green test resting on a selection made to produce one. `test_a_multi_character_reading_does_not_match_its_own_narrator`
asserts the failure persists.