# Audio fixtures — P9.B4 (`gunfire`)

Six 10.24-second clips in this directory, and **two of them are not what their filenames
say.** That is the finding, so read `../../tests/probe_audio_fixtures.py` before trusting
any of this.

## Provenance

Cut from Wikimedia Commons files. Each is one 10.24-second segment, mono 16 kHz — the
AST model's exact input window, so nothing is resampled at test time.

| Segment | Source file | Licence |
|---|---|---|
| `seg_gunshots.wav` | [Gunshots 8.ogg](https://commons.wikimedia.org/wiki/File:Gunshots_8.ogg) | Public domain |
| `seg_machinegun.wav` | [Zeigenbock Kopf - 01 - Machine Gun.ogg](https://commons.wikimedia.org/wiki/File:Zeigenbock_Kopf_-_01_-_Machine_Gun.ogg) | CC BY 3.0 US |
| `seg_fireworks.wav` | [Fireworks in distance 1.ogg](https://commons.wikimedia.org/wiki/File:Fireworks_in_distance_1.ogg) | Public domain |
| `seg_thunder.wav` | [Theres a thunderstorm coming.wav](https://commons.wikimedia.org/wiki/File:Theres_a_thunderstorm_coming.wav) | CC0 |
| `seg_lightning.wav` | [Lightning Strikes at Saturn (PIA14310).wav](https://commons.wikimedia.org/wiki/File:Lightning_Strikes_at_Saturn_(PIA14310).wav) | Public domain (NASA) |
| `seg_speech_control.wav` | `backend/tests/assets/speaker/kara_a.wav` (LibriVox, public domain) | Public domain |

Every licence above is on `scripts/fetch_fixtures.py`'s allowlist. `CC BY-SA 4.0` gunfire
files that exist on Commons — `9 mm gunshot-mike-koenig-123.wav`, `En-au-gunfire.ogg` —
are **refused** by that gate, as ShareAlike is.

`seg_speech_control.wav` is not audio evidence at all. It is the repo's own LibriVox
narration, carried here as a **control**: a front end that cannot put `Speech` at 0.88 on
speech is broken, and that check is what proved the front end before anything was claimed
about gunfire.

## Source digests

Of the full-length files the segments were cut from:

```
f56525e22763d1babb714482ceab52f59ed8974c542a448628e7f2a5b0ddea7d  machinegun_ccby3.ogg
0107c3a14e256c2a4c2e94f7494df6994edba75f4de05fced2d8900cf19b4c0b  gunshots_pd.ogg
efc0811f003b89d43866045cf8288bf9b621aabdbbd161da69279aff3d340752  fireworks1_pd.ogg
0f44c56f44a8a097b532e13dd0c41d4fe269682ba8989007a5b77bcb20923c70  thunder_cc0.wav
011abedbb8b6f34b058fa294c977a640ac5ecb3b3c85dde35970d154d5f4f9dc  lightning_nasa_pd.wav
```

The full-length sources are **not** committed — 7.1 MB against 1.5 MB of segments, and the
segments are all the model ever sees.

## Regenerating

```bash
cd /tmp && mkdir -p audiosrc && cd audiosrc
base="https://commons.wikimedia.org/wiki/Special:FilePath"
curl -sSL "$base/Gunshots%208.ogg"                -o gunshots_pd.ogg
curl -sSL "$base/Zeigenbock%20Kopf%20-%2001%20-%20Machine%20Gun.ogg" -o machinegun_ccby3.ogg
curl -sSL "$base/Fireworks%20in%20distance%201.ogg" -o fireworks1_pd.ogg
curl -sSL "$base/Theres%20a%20thunderstorm%20coming.wav" -o thunder_cc0.wav
curl -sSL "$base/Lightning%20Strikes%20at%20Saturn%20(PIA14310).wav" -o lightning_nasa_pd.wav
sha256sum *.ogg *.wav          # must match the digests above

# -ss is placed AFTER -i on purpose. Fast seek before the input silently returns zero
# bytes on some formats, which produces an empty fixture that looks like a valid one.
cut() { ffmpeg -v error -i "$1" -ss "$2" -t 10.24 -ac 1 -ar 16000 -c:a pcm_s16le "$3" -y; }
cut machinegun_ccby3.ogg 10 seg_machinegun.wav
cut fireworks1_pd.ogg    20 seg_fireworks.wav
cut lightning_nasa_pd.wav 0 seg_lightning.wav
cut gunshots_pd.ogg       0 seg_gunshots.wav
cut thunder_cc0.wav       0 seg_thunder.wav
ffmpeg -v error -i <repo>/backend/tests/assets/speaker/kara_a.wav -t 10.24 \
  -ac 1 -ar 16000 -c:a pcm_s16le seg_speech_control.wav -y
```

Offsets are fixed and not tuned: the gunfire window is where it is because the file is
115 seconds long and 10 seconds in is as good a place as any. Choosing the offset that
produced the best classification would be selecting the evidence to fit the answer.

## Why these are not enough

**One genuine gunfire clip is not a measurement.** `probe_audio_fixtures.py` reports
P(gunfire) of 0.077 for the gunfire clip against 0.030 for fireworks and ~0.000 for
everything else. That is a factor of 2.6 with a single sample on one side of it, and
`Gunshot, gunfire` does not even reach the top class.

Four corpora — Wikimedia Commons, archive.org, Freesound, DVIDS — were searched. They hold
**one** gunfire recording between them under a licence this project accepts. The keyword
searches that failed are worth knowing about too:

- Commons, searching "gunshot": a military march, four presidential speeches, a New York
  bar-admission argument about the *shotgun doctrine*, and **14 `LL-Q1860 …-gunshot.wav`
  pronunciation files** — a person *saying* the word, three of them CC0 and so passing the
  licence gate.
- archive.org, searching gunfire: podcasts and news bulletins.

A pipeline trusting any of those would have shipped a gunshot classifier trained on a
march and a lecture.

**And the trap is worse than mislabelling by convention.** `Zeigenbock Kopf - 01 - Machine
Gun.ogg` is CC BY 3.0 — a proper Creative Commons licence, correctly tagged, and it is
**music**. `Theres a thunderstorm coming.wav` is CC0 and is **a spoken recording**. Neither
is mislabelled by Commons' `LL-Q` convention; both are mislabelled by whoever uploaded
them. **Audio fixture filenames are not evidence of content**, and only running a
classifier over them reveals it — which is why `probe_audio_fixtures.py` asserts what each
clip is, rather than trusting the name.