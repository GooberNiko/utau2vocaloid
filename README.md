# utau2vocaloid 🎤➡️🎹

**Your UTAU voicebank, but it's a VOCALOID now.**

You have an UTAU bank. You have the VOCALOID3 developer kit. You thought "surely there's a button for this."
There was not. Now there is. It's this.

utau2vocaloid reads an UTAU bank (`oto.ini`, VCV / CVVC / CV, Japanese or English), figures out where
every phoneme starts and ends *by actually listening to the audio* (oto.ini is a suggestion, not the law),
writes everything the VOCALOID3 DBTool wants, and then **drives the DBTool for you** so you don't have to
click "Add Articulations" 790 times with your own human finger.

> ⚠️ **No Yamaha files included.** You need your own VOCALOID3 developer kit (`VocaloidDBTool3.exe` + its
> phonetic dictionaries). We do not have it, we will not send it, please do not ask the README.

---

## 🚀 Quick start (the "I just want it to sing" path)

1. Put this `utau2vocaloid` folder **inside your devkit folder** (next to `VocaloidDBTool3.exe`).
2. `pip install -r requirements.txt`
3. Double-click **`utau2vocaloid GUI.bat`**.
4. Pick your UTAU bank → **Convert** → **Build DB**. Go make a sandwich. 🥪 (Building takes a few minutes.
   The DBTool window will pop up and click itself. This is normal. Do not fight it. It is not haunted.)
5. Optional: **Test in VOCALOID** tab → it mounts your new DB in the VOCALOID4 Editor for Developer,
   renders a test song, and checks it for problems. Close the editor first; VOCALOID only allows one of itself.

That's it. That's the README. …okay fine, there's more below.

## 🤔 What it actually does

| Problem | What utau2vocaloid does about it |
|---|---|
| Your bank is a beautiful mess of folders | Finds every `oto.ini` at any depth, reads `prefix.map`, figures out pitches (`C3`, `A#3`…) and voice colours (`Soft`, `弱`, `囁`…) by itself |
| Your bank is **CV only** and VOCALOID wants transitions | **Fake CVVC**: splices the missing `[V C]`, `[V V]`, `[V ん]` joins from two samples (phase-aligned, so no robot clicks). CV banks can sing legato now. Congrats |
| Some transitions just… don't exist | Relabels close relatives (`[a k']` from `[a k]`), mutes onsets for `[Sil C]`, fades vowels for `[V Sil]` |
| Long notes sounded like a skipping CD | Held-vowel loops follow the vowel to where it *really* ends, and short ones get several takes stitched together (~0.8 s), level-matched and EQ'd to match the notes around them |
| "ke-ko" came out as "ke-ho" | CV entries whose oto starts right on the burst get their silent closure back |
| `h` sounded like the raw sample | VOCALOID doesn't re-pitch unvoiced sounds, so `h` becomes breath-shaped noise at a sensible volume (not a vowel-loud hiss, we learned that one the hard way) |
| ん before b / t / k sounded wrong | Every ん flavour (`N\ m n N N' J m'`) gets held-note loops and the units VOCALOID asks for, plus `[Sil N\]` for phrases that start on ん |
| A unit buzzes like a bee 🐝 | After building, it **reads back the pitch the DBTool analysed** for every unit; if one is an octave off (= buzzy half-pitch undertone), it fixes it and rebuilds. Automatically. While you eat the sandwich |
| One recording just renders badly no matter what | `--avoid "g' i"` rebuilds that unit from other recordings instead |
| Notes jump in volume | Every unit is scaled to the bank's usual vowel level |
| Your bank is a formant synth with no silence anywhere | Fine. `Sil` is only taken where it's actually silent |

The DBTool's file formats were worked out from the DBTool itself — nerd details in [FORMATS.md](FORMATS.md).

## 🌬️ Extras (breaths, rolled r, end breaths)

If your bank has bonus samples, they become phonemes you can type in the editor:

| Type this | What it is | Made from (UTAU alias) |
|---|---|---|
| `br1` … `br5` | a breath, on its own note | `息1`, `breath2`, or `b1`… in an extras folder |
| `brE` | breath **out** at the end of a phrase | `a R息`, `i R息`… (each vowel its own) |
| `brI` | breath **in** | `a R吸`… |
| `rr` | rolled r (yes, really) | `巻`, `rr` |
| *(nothing to type)* | natural vowel endings `[a Sil]` | `a R`, `i R`… |

Joins the bank never recorded are spliced, so a breath can go right before or after any sung note
(`br1` then `sa`, `a` then `brE`, `rr a`). Extras are picked up from their bonus folder even if you only
selected the singing folders. Every `report.txt` lists which extra came from which sample.
Don't want them? Untick **Extras** in the GUI, or `--no-extras`.

## 🎨 Voice colours (EVEC)

Banks with several colours (Standard / Soft / Whisper…):

- `--colors main` (default) — just the main colour.
- `--colors evec` — **all colours in one DB**. The main one sings with normal phonemes, the next with
  `a#1 k#1 …`, then `#2`… Type the suffixed phonemes in the editor to switch colour.
  (The DBTool thinks `#` starts a comment, so we sneak `aX1` past it and rename it to `a#1` inside the
  built DB. It never suspected a thing.)
- `--colors merge` — treat all colours as one. Chaos mode. Not recommended.

## 🇬🇧 English banks

Arpasing and X-SAMPA-style banks (e.g. Kasane Teto English: `wE`, `e d`, `- skl`, `oU l-`) convert with
English coverage checks and gap filling. Build with `--language English` (the GUI picks it for you when
the bank looks English).

## 🧰 Requirements

- Windows (the DBTool is a Windows program; `convert` alone runs anywhere)
- Python 3.10+
- `pip install -r requirements.txt` (numpy; pywinauto + pywin32 for building)
- The VOCALOID3 developer kit: `VocaloidDBTool3.exe` + its `Japanese Dictionary` / `English Dictionary`
  folders. Put this folder inside the devkit folder, or pass `--dict` and `--dbtool`.
- Optional: VOCALOID4 Editor for Developer, for the test-render tools.

## ⌨️ Command line (for people who think GUIs are for cowards)

```bat
:: 1. convert an UTAU bank
python utau2vocaloid.py convert "C:\path\to\UTAU bank" "C:\out\MyBank_seg"

:: 2. build the singer DB with the DBTool (a few minutes; it clicks itself)
python utau2vocaloid.py build "C:\out\MyBank_seg" "C:\out\MyBank\MyBank" --name "MyBank"

:: 3. paranoia
python utau2vocaloid.py validate "C:\out\MyBank_seg"
```

`convert` writes:

- `report.txt` — coverage of the standard diphone set, what was recorded / relabelled / spliced, extras, and
  every oto entry that got skipped *and why* (read this when something sounds weird, it's usually in there)
- `manifest.csv` — where every single unit came from
- `dictionary.txt` — the phonetic dictionary to create the singer DB from

Useful `convert` options:

| option | |
|---|---|
| `--groups REGEX` | only use these folders/suffixes, e.g. `--groups "^Standard"` |
| `--merge-groups` | treat every folder as one sample set |
| `--keep N` | samples kept per unit and pitch (default 1) |
| `--lang ja\|en` | force the language (default: it guesses, and it's usually right) |
| `--dict FILE` | phonetic dictionary `.txt` (default: the devkit's one for the language) |
| `--colors main\|evec\|merge` | voice colours, see above |
| `--avoid "C V"` | don't use this unit's recording; rebuild it from others (for the one unit that's cursed) |
| `--brighten PH=DB` | lift the treble of a dull vowel, e.g. `--brighten o=3` |
| `--no-extras` | skip breaths / rolled r / vowel endings |
| `--no-splice` | don't fake missing transitions |
| `--no-fill` | don't relabel / mute / fade to fill missing units |
| `--no-normalize` | keep each recording's own loudness |
| `--no-extend-dict` | don't add `b`, `b'`, `p\'` (the devkit's Japanese dictionary forgot them) |

Building by hand instead: DBTool → *File → New singer database* from `dictionary.txt`, then
*File → Automatic Segmentation*, point it at the converted folder, run the steps, wait, contemplate life.

## 🧪 Testing in the VOCALOID4 Editor for Developer (`tools/`)

The dev editor only sings the voices listed in its installed `DB_Dev.ini` (which needs admin to edit, lol).
So `slot.py` borrows one of your existing voice slots with a folder link and **keeps your real DB safe**
next to it as `<folder>__orig`.

| tool | what it does |
|---|---|
| `tools/cycle.py <bank> <tag> [options]` | convert → build → mount → render → check, in one go |
| `tools/render.py <voice no> <out.wav>` | writes a test song (all syllables, legato, long notes, every ん; `--song en` / `--song extras` for English and breaths) and renders it in the dev editor, plus the `.msd` analysis |
| `tools/render_qa.py <song.wav>` | finds doubled plosive bursts, level/timbre jumps where units meet, clicks |
| `tools/msd.py <song.msd>` | prints which unit VOCALOID used when (held notes show up as `[o o]`) |
| `tools/qa.py <converted folder>` | checks a converted folder before building: loop length/seams, dips, cut points, loudness |
| `tools/slot.py mount <voice no> <DB folder>` | points a voice slot at a test DB |
| `tools/slot.py restore <voice no>` | **gives you your voice back** ← remember this one |

## 🩹 Troubleshooting

- **"VOCALOID4 is already running."** Yes. Yes it is. Close it, then render.
- **The DBTool window is clicking by itself.** That's us. Hands off the keyboard for a minute.
- **A syllable sounds weird.** Look it up in `report.txt` (skipped entries + reasons). If one unit is just
  bad, `--avoid "C V"` it.
- **Something buzzes.** The build log says if the DBTool read any unit an octave off. It retries with a fix;
  if it still lists units afterwards, `--avoid` those.
- **I mounted a test DB and now my real voice is gone!!** It's not gone. `python -I tools/slot.py restore <n>`.
- **Long notes still sound loopy.** Your bank's vowels may just be very short. Record longer vowels. We
  believe in you.

## 🚧 Known limits

- Spliced / relabelled units are approximations. Real recordings always win when they exist.
- Phrase-start vowels are built from the steady vowel (the recorded ones faded in slowly in VOCALOID).
- `[d' M]`, `[t' M]` (dyu, tyu) and `[w o]` only exist if your bank recorded them.
- Some units can stay octave-confused in the DBTool even after the automatic fix (the build log names them).

## 🙏 Please

Only convert and share banks whose terms allow it, **credit the original voice provider**, and don't
redistribute Yamaha's developer kit or anything made from VOCALOID's own voice data. Be cool. 😎

## License

MIT, see [LICENSE](LICENSE). VOCALOID is a trademark of Yamaha Corporation; this project is not affiliated
with or endorsed by Yamaha. UTAU is by Ameya/Ayame. Teto is a chimera. Nobody here is affiliated with anybody.
