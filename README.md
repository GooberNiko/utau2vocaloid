# utau2vocaloid

Port UTAU voicebanks to VOCALOID3 singer databases.

It reads an UTAU bank (`oto.ini`, any folder layout, VCV / CVVC / CV, Japanese or English Arpasing),
finds the phoneme boundaries in the audio, and writes the input files of the VOCALOID3 DBTool
(`.wav` / `.trans` / `.seg` / `.as0..`). It can then drive the DBTool to build the singer database for you.

**No Yamaha files are included.** You need your own copy of the VOCALOID3 developer kit
(`VocaloidDBTool3.exe` and its phonetic dictionaries).

## What it does

- **Any UTAU layout.** `oto.ini` files at any depth, `prefix.map`, per-folder pitch/colour suffixes
  (`C3`, `A#3`, `C3SF`, `↑`, `弱`, `囁` ...) are detected automatically. Kana and romaji aliases.
- **VCV, CVVC and CV.** VCV/CVVC transitions are cut from the recordings. For transitions the bank never
  recorded, it fills the gap:
  - closures that sound alike are relabelled (`[a k']` from `[a k]`),
  - `[Sil C]` onsets are made by muting what comes before the consonant,
  - `[V Sil]` endings by fading the vowel out,
  - **fake CVVC for CV banks:** missing `[V C]`, `[V V]`, `[V N\]` and `[N C]` are spliced from two
    samples (the end of a long vowel joined to the start of the next sound, phase-aligned crossfade for
    voiced joins, a clean stop for plosives and fricatives). A plain CV bank gets the transitions it
    needs to sing legato.
- **Pitches and voice colours.** Folders/suffixes are split into a pitch (`C3`, `F#3` ...) and a colour
  (`Standard`, `Soft` ...). Every pitch of a colour is added to the same units, so the DB holds several
  pitches and VOCALOID picks the closest. Other colours get EVEC-style phoneme suffixes (`a#1`, `k'#1`)
  — see *Known limits*.
- **Smooth long notes.** Stationaries (what VOCALOID loops on held notes) follow each vowel through the
  recording until it really ends, instead of stopping at the oto region. Length wins over small timbre
  differences (a short loop is heard repeating); when a bank's vowels are short (VCV strings), several
  takes of the same vowel at the same pitch are joined (level-matched, phase-aligned crossfades) into
  one loop of ~0.8 s.
  Each loop is cut from a stretch of steady level and EQ'd toward the vowel as it sounds where the
  units hand over to it, so a held note keeps the colour of the syllable before it.
- **Consonant starts.** A CV entry whose oto offset sits on the plosive release (or inside a nasal's
  murmur) gets the closure / murmur back from the recording; without it VOCALOID ran the previous vowel
  straight into the burst.
- **Unit marks.** Plosive cut points sit just before the burst; vowel marks move to where the vowel
  sounds most like the bank's usual one.
- **Extras.** Breaths and other bonus samples become phonemes you can type in the editor:
  `息1`.. / `breath2` (or `b1`.. in an extras folder) -> `br1`..`br5`, `a R息` (a vowel ending in a breath
  out) -> `[a brE]`, `a R吸` (a pause, then a breath in) -> `brI`, `巻` / `rr` -> a rolled `rr`, and plain
  `a R` endings are used for `[a Sil]`. Joins the bank never recorded are spliced, so a breath can sit
  right before or after any sung note (`br1` then `sa`, `a` then `brE`, `rra`). Extras are taken from
  their bonus folder even when `--groups` picks only the singing folders; `--no-extras` skips them.
- **Unvoiced h.** VOCALOID plays `h`/`C`/`p\` back as recorded, without moving them to the note's pitch,
  so a breathy recorded h kept its recorded pitch. These are replaced by noise with the same spectral
  shape.
- **Even loudness.** Every unit is scaled so its vowels sit at the bank's usual level for that vowel, so
  notes don't jump in volume where units are joined.
- **Banks without silence** (formant / synthetic voices) are handled: `Sil` is only taken where the
  audio really is silent.
- **DBTool pitch check.** After building, the pitch the DBTool analysed for every unit is read back
  from the DB. A transition it read an octave high renders with a buzzing half-pitch undertone, so
  those units get their fundamental raised and the DB is built once more (automatic).
- **Validation.** The output is checked the way the DBTool parses it before you build.
- **DBTool automation** (`build`): creates the singer DB from the generated dictionary and runs
  *Add Stationaries → Optimize EpR Guides → Add Articulations* on a private copy of the DBTool, so your
  own `VocaloidDBTool.ini` is never touched.

The file formats were worked out from the DBTool itself; see [FORMATS.md](FORMATS.md).

## Requirements

- Windows (the DBTool is a Windows program; `convert` itself runs anywhere)
- Python 3.10+
- `pip install -r requirements.txt` (numpy; pywinauto + pywin32 for `build`)
- the VOCALOID3 developer kit: `VocaloidDBTool3.exe` plus its `Japanese Dictionary` /
  `English Dictionary` folders. Put this folder inside the devkit folder, or pass `--dict` and `--dbtool`.

## Usage

### Window

Double-click `utau2vocaloid GUI.bat` (or run `python gui.py`): choose the UTAU bank, pick which folders to
use, then **Convert**, **Build DB**, and **Build & render test** to hear it in the VOCALOID4 Editor for
Developer. Every button runs the command line below and shows its output; settings are remembered.

### Command line

```bat
:: 1. convert an UTAU bank
python utau2vocaloid.py convert "C:\path\to\UTAU bank" "C:\out\MyBank_seg"

:: 2. build the singer DB with the DBTool (takes a few minutes)
python utau2vocaloid.py build "C:\out\MyBank_seg" "C:\out\MyBank\MyBank" --name "MyBank"

:: check a folder again at any time
python utau2vocaloid.py validate "C:\out\MyBank_seg"
```

`convert` writes `report.txt` (coverage of the standard Japanese diphone set, what was recorded,
relabelled or spliced, and which oto entries were skipped and why), `manifest.csv` (where every unit came
from) and `dictionary.txt` (the phonetic dictionary to create the singer DB from).

Useful `convert` options:

| option | |
|---|---|
| `--groups REGEX` | only use these folders/suffixes, e.g. `--groups "^Standard"` |
| `--merge-groups` | treat every folder as one sample set |
| `--keep N` | samples kept per unit and pitch (default 1) |
| `--lang ja\|en` | force the language (default: detected) |
| `--dict FILE` | phonetic dictionary `.txt` (default: the devkit's dictionary for the language) |
| `--no-splice` | don't fake missing transitions |
| `--no-fill` | don't relabel / mute / fade to fill missing units |
| `--no-normalize` | keep each recording's own loudness |
| `--colors main\|evec\|merge` | banks with several voice colours: convert only the main one (default), give the others `#N` phonemes (see *Known limits*), or treat all as one |
| `--no-extras` | skip breaths / rolled r / vowel endings |
| `--avoid "C V"` | don't use this unit's recording (rebuild it by relabelling/splicing), for a unit that renders badly |
| `--no-extend-dict` | don't add `b`, `b'`, `p\'` (missing from the devkit's Japanese dictionary) |

Building by hand instead of `build`: in the DBTool, *File → New singer database* from `dictionary.txt`,
then *File → Automatic Segmentation*, point it at the converted folder and run the steps.

## Testing a bank in the VOCALOID4 Editor for Developer (`tools/`)

| tool | |
|---|---|
| `tools/render.py <voice no> <out.wav>` | writes a test song (all common syllables, legato vowels, long notes, ん in every context) as .vsqx, opens it in `VOCALOID4_Dev.exe` and exports the wav plus the `.msd` analysis file |
| `tools/msd.py <song.msd>` | prints which unit VOCALOID used when (stationaries appear as `[o o]`) |
| `tools/render_qa.py <song.wav>` | finds doubled plosive bursts, level/timbre jumps where two units meet inside a vowel, and clicks |
| `tools/qa.py <converted folder>` | checks a converted folder before building: stationary length/seams, vowel-vowel dips, plosive cut points, join match, loudness |
| `tools/slot.py mount <voice no> <DB folder>` | the dev editor only sings the voices in its installed `DB_Dev.ini` (admin to edit); this points one voice slot at a test DB with a folder junction, `restore` puts it back |
| `tools/cycle.py <bank> <tag> [convert options]` | convert → build → mount → render → check, in one go |

## Known limits

- **Voice colours (EVEC).** `--colors evec` puts every colour in one DB: the main colour sings with
  plain phonemes, the next with `a#1 k#1 ...`, then `#2` ... (type the suffixed phonemes in the editor).
  The DBTool reads `#` in a dictionary as a comment, so `dictionary.txt` holds `aX1` and `build` renames
  them to `a#1` in the new DB's phoneme table before adding units. Default is still the main colour only.
- Vowel phrase starts are built from the steady vowel rather than taken from the first syllable of a
  recording string: rendered, the recorded ones faded in slowly (up to 200 ms).
- English: Arpasing and X-SAMPA-style banks (e.g. Kasane Teto English: `wE`, `e d`, `- skl`, `oU l-`) convert,
  with English coverage and gap filling. Build English DBs with `build ... --language English`.
- Spliced and relabelled units are approximations; recorded transitions always win when they exist.
- `[d' M]`, `[t' M]` (dyu, tyu) and `[w o]` are only produced when the bank recorded them.

## Please

Only convert and share banks whose terms allow it, credit the original voice provider, and don't
redistribute Yamaha's developer kit or anything built from VOCALOID's own voice data.

## License

MIT, see [LICENSE](LICENSE). VOCALOID is a trademark of Yamaha Corporation; this project is not
affiliated with or endorsed by Yamaha.
