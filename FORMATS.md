# VOCALOID3 DBTool input formats (reverse-engineered)

Source: `VocaloidDBTool3.exe` (PE32, MSVC9, MFC). Function addresses are VAs in that exe.
Everything here was read from the disassembly, not guessed.

## Folder layout

"Segmentate Folder" points at one flat folder. Each recording `<name>.wav` comes with:

| file | written by | purpose |
|---|---|---|
| `<name>.wav` | you | mono PCM, **must** equal the singer sampling rate (`singer.inf`, 44100) |
| `<name>.trans` | you | phonetic transcription + list of units to add |
| `<name>.seg` | Step 1 (or you) | phoneme-level segmentation |
| `<name>.as0`, `.as1`, ... | Step 4 (or you) | one per directive in `.trans`, in order (`<name>.as` + index) |

## `.trans` (parser at 0x602070)

* Read line by line with `fgets` (max 0x400 chars), each line trimmed of `" \t\n\r"`, empty lines skipped. Max file size 512 KB.
* Lines starting with `/` before the phoneme line are kept as a "generic transcription" header (optional).
* **First other line** = the full phoneme sequence of the file, whitespace separated (istringstream).
* **Every later line** = a directive: `[ph1 ph2 ...] params`
  * the text between `[` and `]` is the unit; it must occur in the phoneme sequence.
  * 1 phoneme = **stationary**, 2+ = **articulation**. A file must not mix both.
  * params: none, 1 or 4.
    * 1 param: `occurrence` (int, 1-based; which match in the phoneme line). Default 1.
    * 4 params: `f0 f1 f2 occurrence`. The floats default to `90.0, 0.6, -1200.0`. Not needed.
* All phonemes must exist in the DB's phonetic dictionary.

```
Sil k a
[Sil k]
[k a]
```

## `.seg` (writer 0x6003c0, reader 0x600ab0)

```
nPhonemes 3
articulationsAreStationaries = 0
phoneme		BeginTime		EndTime
===================================================
Sil		0.000000		0.105000
k		0.105000		0.160000
a		0.160000		0.600000
```
* optional first line `REVISED!` (manually checked flag).
* `articulationsAreStationaries = 1` for files whose directives are stationaries.
* times are in seconds; segments must be contiguous (no gaps or overlaps) and non-negative; phonemes must match the `.trans` line.

## `.as<N>` (writer 0x5fc5f0, reader 0x5fed30, validator 0x5fad70)

When reading, the parser strips all whitespace, so `first phoneme:` matches the key `firstphoneme`. A file holds one block only.

Two block types are accepted.

**V2 `articulation asr segmentation`** (1 or 2 phonemes). **Use this one.**
```
articulation asr segmentation
{
	first phoneme: "k";
	last phoneme: "a";
	cut offset: 0;
	cut length: 26460;
	begin mark time: 0.120000000;
	note align mark time: 0.160000000;
	end mark time: 0.400000000;
	revised: true;
	left region voiced: false;
	right region voiced: true;
};
```
* A stationary is `first == last`, `note align mark time: -1.0`, and equal left/right voiced flags. The reader checks `first==last && notealign < 0 && lv == rv`.
* For a diphone, begin and end are marks inside the 1st and 2nd phoneme, and note align is the transition between them.

**V3 `nphone art segmentation`** (`phns: [...]`, `boundaries: [...]`, `voiced: [...]`; 2n-1 boundaries and 2n-2 voiced flags, or 2/1 for n=1).
**Avoid it for stationaries.** Verified with Frida: the tool's list parser (0x5fd6a0) leaves the phoneme name unterminated (`"aý4"`). The phoneme id then becomes -1, the tree node gets a NULL name, and "Add Stationaries To Database" crashes in the tree save (0x426be8).

Common fields:
* `cut offset`, `cut length`: **samples** in the wav (the region used for analysis).
* All mark times are **seconds relative to cut offset**.
* `.as<N>` is read as `<wav base>.as<N>`, where N is the directive's index in the `.trans` file.

## Phoneme dictionaries

The DB `.dat` has a `PHDC` chunk: u32 size, u32 ?, u32 count, then count × 31-byte records (30-byte name + 1-byte unvoiced flag).
The Japanese dictionary in this devkit has **no `b`/`b'`** (and no `h\`, `p\'`), so units using them are skipped unless you add them to the dictionary and rebuild the DB.
`Japanese Dictionary/Japanese_Dictionary_b.txt` is the same dictionary plus `b`, `b'` and `p\'`: create the singer DB from it (File → New singer database) and convert with `--dict` pointing at it.
