"""Readers/writers for VOCALOID3 DBTool input files (.trans, .seg, .as<N>) and phonetic dictionaries.

Formats were reverse-engineered from VocaloidDBTool3.exe, see FORMATS.md.
"""
import re
import struct
from dataclasses import dataclass, field


# ---------------------------------------------------------------- dictionary

@dataclass
class PhoneticDict:
    voiced: dict = field(default_factory=dict)   # phoneme -> bool
    groups: dict = field(default_factory=dict)   # phoneme -> group name

    def __contains__(self, ph):
        return ph in self.voiced

    def is_voiced(self, ph):
        return self.voiced.get(ph, ph not in ('Sil', '?', 'Asp'))


def load_dictionary_txt(path):
    """Parse a DBTool phonetic dictionary .txt (phonetic_group(name=..., voiced=...): + indented phonemes)."""
    d = PhoneticDict()
    group, voiced = None, None
    lines = open(path, encoding='latin-1').read().splitlines(True)
    ours = bool(lines) and 'utau2vocaloid' in lines[0]      # our files write "a#1" as "aX1" (EVEC_STANDIN)
    for raw in lines:
        if ours:
            raw = re.sub(r'(\S)X(\d+)\s*$', lambda m: m.group(1) + '#' + m.group(2) + '\n', raw)
        # "#" after whitespace starts a comment ("l0 # YH20021108"); inside a name it is EVEC ("a#1")
        line = re.sub(r'(^|\s)#.*$', '', raw.rstrip('\r\n')).rstrip()
        if not line.strip():
            continue
        m = re.match(r'\s*phonetic_group\(name="([^"]+)",\s*voiced=(true|false)\)\s*:', line)
        if m:
            group, voiced = m.group(1), m.group(2) == 'true'
            continue
        if not raw[0].isspace():          # any other top-level block ends the groups
            group = None
            continue
        if group is not None:
            # "p\\'" (copied from an escaped error message) is the phoneme p\': no phoneme has "\\"
            ph = line.strip().replace('\\\\', '\\')
            d.voiced[ph] = voiced
            d.groups[ph] = group
    return d


def load_dictionary_dat(path):
    """Read the PHDC chunk of a DB .dat/.tree (31-byte records: name + unvoiced flag)."""
    data = open(path, 'rb').read()
    i = data.find(b'PHDC')
    if i < 0:
        raise ValueError('no PHDC chunk in %s' % path)
    _size, _unk, n = struct.unpack('<III', data[i + 4:i + 16])
    d = PhoneticDict()
    p = i + 16
    for _ in range(n):
        rec = data[p:p + 31]
        p += 31
        d.voiced[rec[:30].split(b'\0')[0].decode('latin-1')] = rec[30] == 0
    return d


def extend_dictionary(d, extra):
    """Copy of d plus {phoneme: (group, voiced)} entries it lacks."""
    out = PhoneticDict(dict(d.voiced), dict(d.groups))
    for ph, (grp, v) in extra.items():
        if ph not in out:
            out.voiced[ph], out.groups[ph] = v, grp
    return out


EVEC_STANDIN = 'X'     # "#" starts a comment in DBTool dictionaries: "a#1" is written "aX1", then patched


def write_dictionary(path, d, suffixes=()):
    """Write d as a DBTool dictionary .txt, plus every non-silence phoneme again with each suffix
    ("a" -> "a#1"), so a singer DB created from it accepts EVEC-style colours. The DBTool's dictionary
    reader cuts lines at "#", so suffixed names are written with EVEC_STANDIN ("aX1"); `build` renames
    them in the new DB's phoneme table (patch_evec_names) before any unit is added."""
    groups = {}
    for ph, v in d.voiced.items():
        g = d.groups.get(ph) or ('Voiced' if v else 'Unvoiced')
        groups.setdefault((g, v), []).append(ph)
    lines = ['# written by utau2vocaloid: create the singer database from this file', '']
    for (g, v), phs in groups.items():
        lines.append('phonetic_group(name="%s", voiced=%s):' % (g, 'true' if v else 'false'))
        lines += ['\t' + p for p in phs]
        if g != 'Silence':
            lines += ['\t' + p + s.replace('#', EVEC_STANDIN) for s in suffixes for p in phs]
        lines.append('')
    with open(path, 'w', encoding='latin-1', newline='\r\n') as f:
        f.write('\n'.join(lines))


def patch_evec_names(db_path):
    """In <db>.dat and <db>.tree, rename "aX1" -> "a#1" inside the phoneme dictionary chunk (PHDC, which
    holds the PHG2 group lists too). Same length, so no offsets change. Returns the number renamed."""
    sx = EVEC_STANDIN.encode()

    def fix(name):
        m = re.fullmatch(rb'([\x21-\x7e]+)' + sx + rb'(\d+)', name)
        return m.group(1) + b'#' + m.group(2) if m else None
    n = 0
    for ext in ('.dat', '.tree'):
        fn = db_path + ext
        with open(fn, 'rb') as f:
            data = bytearray(f.read())
        i = data.find(b'PHDC')
        if i < 0:
            continue
        size, _, count = struct.unpack('<III', data[i + 4:i + 16])
        for k in range(count):                       # PHDC: 31-byte records, 30-byte NUL-padded name
            p = i + 16 + 31 * k
            name = bytes(data[p:p + 30]).split(b'\0')[0]
            new = fix(name)
            if new:
                data[p:p + len(new)] = new
                n += 1
        g = data.find(b'PHG2', i, i + size)          # PHG2 group lists: u32 length + name
        while 0 <= g < i + size:
            for m in re.finditer(rb'([\x02-\x1e])\x00\x00\x00', bytes(data[g:i + size])):
                ln, p = m.group(1)[0], g + m.end()
                new = fix(bytes(data[p:p + ln]))
                if new and len(new) == ln:
                    data[p:p + ln] = new
                    n += 1
            break
        with open(fn, 'wb') as f:
            f.write(bytes(data))
    return n


def db_pitches(db_path):
    """The pitch the DBTool analysed for every unit of a built DB: {('art', p1, p2) | ('stat', p):
    median first-harmonic frequency (Hz) of the unit's frames}. Each FRM2 frame holds its partials'
    frequencies (u32 count at +28, f32 frequencies from +32)."""
    import os
    import numpy as np
    out = {}
    root = os.path.join(os.path.abspath(db_path), 'voice')

    def name(s):
        return re.sub(r'%(\d+)%', lambda m: chr(int(m.group(1))), s)
    for kind, key in (('articulation', 'art'), ('stationary', 'stat')):
        base = os.path.join(root, kind)
        for dp, _, files in os.walk(base):
            for fn in files:
                if fn.endswith('.dat'):
                    continue
                b = open(os.path.join(dp, fn), 'rb').read()
                f1 = []
                for m in re.finditer(b'FRM2', b):
                    i = m.start()
                    if i + 36 <= len(b) and 0 < struct.unpack_from('<I', b, i + 28)[0] < 4000:
                        v = struct.unpack_from('<f', b, i + 32)[0]
                        if 40 < v < 2000:
                            f1.append(v)
                if not f1:
                    continue
                parts = [name(x) for x in os.path.relpath(os.path.join(dp, fn), base).split(os.sep)]
                k = ('art',) + tuple(parts) if key == 'art' else ('stat', parts[1] if len(parts) > 2 else parts[0])
                out[k] = float(np.median(f1))
    return out


DICT_NAMES = {'ja': ('Japanese Dictionary', 'Japanese_Dictionary.txt'),
              'en': ('English Dictionary', 'english_phonetic_dictionary_20061220.txt')}


def dictionary_language(path):
    """'ja', 'en' or None for a DBTool phonetic dictionary .txt (by name, then by its phonemes)."""
    import os
    name = os.path.basename(path).lower()
    if 'japan' in name or name.startswith(('ja_', 'jp')):
        return 'ja'
    if 'english' in name or name.startswith('en_'):
        return 'en'
    try:
        txt = open(path, encoding='latin-1').read(20000)
    except OSError:
        return None
    if 'phonetic_group(' not in txt:
        return None
    phs = {ln.strip() for ln in txt.splitlines() if ln.startswith('\t')}
    if 'N\\' in phs and 'M' in phs:
        return 'ja'
    if '@' in phs and 'Q' in phs:
        return 'en'
    return None


def find_dictionary(lang, roots):
    """The devkit's phonetic dictionary .txt for lang ('ja' / 'en'): the usual place in each root first
    (<root>/Japanese Dictionary/Japanese_Dictionary.txt), then any dictionary .txt up to three folders
    deep. None if there is none."""
    import os
    roots = [os.path.abspath(r) for r in roots if r and os.path.isdir(r)]
    folder, fname = DICT_NAMES.get(lang, (None, None))
    for r in roots:
        if folder and os.path.exists(os.path.join(r, folder, fname)):
            return os.path.join(r, folder, fname)
    found = []
    for r in roots:
        base = r.count(os.sep)
        for dp, dn, fn in os.walk(r):
            if dp.count(os.sep) - base >= 3:
                dn[:] = []
            dn[:] = [d for d in dn if not d.endswith('_seg') and d not in ('render_tests', '__pycache__', '.git')]
            for f in fn:
                if f.lower().endswith('.txt') and f.lower() != 'dictionary.txt' \
                        and dictionary_language(os.path.join(dp, f)) == lang:
                    found.append(os.path.join(dp, f))
        if found:
            break
    # the plain one over variants ("Japanese_Dictionary.txt" before "Japanese_Dictionary_b.txt")
    return min(found, key=lambda f: (len(os.path.basename(f)), f)) if found else None


def load_dictionary(path):
    if path.lower().endswith(('.dat', '.tree')):
        return load_dictionary_dat(path)
    return load_dictionary_txt(path)


# ---------------------------------------------------------------- .trans

def write_trans(path, phonemes, directives):
    """directives: list of (unit_phonemes, occurrence)."""
    lines = [' '.join(phonemes)]
    for unit, occ in directives:
        lines.append('[%s]%s' % (' '.join(unit), '' if occ == 1 else ' %d' % occ))
    with open(path, 'w', encoding='ascii', newline='\r\n') as f:
        f.write('\n'.join(lines) + '\n')


def read_trans(path):
    phonemes, directives = None, []
    for raw in open(path, encoding='ascii'):
        line = raw.strip(' \t\r\n')
        if not line:
            continue
        if phonemes is None:
            if line.startswith('/'):
                continue
            phonemes = line.split()
            continue
        m = re.match(r'\[([^\]]*)\](.*)$', line)
        if not m:
            raise ValueError('%s: invalid articulation-to-add directive: %r' % (path, line))
        unit, params = m.group(1).split(), m.group(2).split()
        if not unit:
            raise ValueError('%s: directive without phonemes' % path)
        if len(params) not in (0, 1, 4):
            raise ValueError('%s: directive needs 0, 1 or 4 params' % path)
        occ = int(params[-1]) if params else 1
        directives.append((unit, occ))
    return phonemes or [], directives


def find_occurrence(phonemes, unit, occ):
    n = 0
    for i in range(len(phonemes) - len(unit) + 1):
        if phonemes[i:i + len(unit)] == unit:
            n += 1
            if n == occ:
                return i
    return -1


# ---------------------------------------------------------------- .seg

def write_seg(path, segments, stationaries=False, revised=True):
    """segments: list of (phoneme, begin_s, end_s)."""
    out = []
    if revised:
        out.append('REVISED!')
    out.append('nPhonemes %d' % len(segments))
    out.append('articulationsAreStationaries = %d' % (1 if stationaries else 0))
    out.append('phoneme\t\tBeginTime\t\tEndTime')
    out.append('=' * 51)
    for ph, b, e in segments:
        out.append('%s\t\t%.6f\t\t%.6f' % (ph, b, e))
    with open(path, 'w', encoding='ascii', newline='\n') as f:
        f.write('\n'.join(out) + '\n')


def read_seg(path):
    segs, stationaries, revised = [], False, False
    n = None
    for raw in open(path, encoding='ascii'):
        line = raw.strip()
        if not line or line.startswith('==='):
            continue
        if line == 'REVISED!':
            revised = True
        elif line.startswith('nPhonemes'):
            n = int(line.split()[1])
        elif line.startswith('articulationsAreStationaries'):
            stationaries = line.split('=')[1].strip() == '1'
        elif line.startswith('phoneme'):
            continue
        else:
            parts = line.split()
            if len(parts) != 3:
                raise ValueError('%s: invalid .seg line %r' % (path, line))
            segs.append((parts[0], float(parts[1]), float(parts[2])))
    if n is None or n != len(segs):
        raise ValueError('%s: nPhonemes mismatch' % path)
    return segs, stationaries, revised


# ---------------------------------------------------------------- .as<N>

@dataclass
class ArtSeg:
    phns: list
    cut_offset: int          # samples
    cut_length: int          # samples
    boundaries: list         # seconds, relative to cut_offset
    voiced: list
    revised: bool = True


def write_as(path, a: ArtSeg):
    """Write an .as<N> block.

    1-2 phonemes use the V2 'articulation asr segmentation' block: the DBTool's reader for the
    newer 'nphone art segmentation' block mangles phoneme names (adding a stationary written that
    way crashes the tool), so nphone is only used for units V2 can't express."""
    n = len(a.phns)
    need_b = 2 if n == 1 else 2 * n - 1
    need_v = 1 if n == 1 else 2 * n - 2
    assert len(a.boundaries) == need_b and len(a.voiced) == need_v, (a, need_b, need_v)
    tf = lambda v: 'true' if v else 'false'
    if n <= 2:
        if n == 1:      # stationary: first == last, note align < 0, equal voiced flags
            begin, align, end = a.boundaries[0], -1.0, a.boundaries[1]
            lv = rv = a.voiced[0]
        else:
            begin, align, end = a.boundaries
            lv, rv = a.voiced
        out = ['articulation asr segmentation', '{',
               '\tfirst phoneme: "%s";' % a.phns[0],
               '\tlast phoneme: "%s";' % a.phns[-1],
               '\tcut offset: %d;' % a.cut_offset,
               '\tcut length: %d;' % a.cut_length,
               '\tbegin mark time: %.9f;' % begin,
               '\tnote align mark time: %.9f;' % align,
               '\tend mark time: %.9f;' % end,
               '\trevised: %s;' % tf(a.revised),
               '\tleft region voiced: %s;' % tf(lv),
               '\tright region voiced: %s;' % tf(rv),
               '};']
    else:
        out = ['nphone art segmentation', '{',
               '\tphns: [%s];' % ', '.join('"%s"' % p for p in a.phns),
               '\tcut offset: %d;' % a.cut_offset,
               '\tcut length: %d;' % a.cut_length,
               '\tboundaries: [%s];' % ', '.join('%.9f' % b for b in a.boundaries),
               '\trevised: %s;' % tf(a.revised),
               '\tvoiced: [%s];' % ', '.join(tf(v) for v in a.voiced),
               '};']
    with open(path, 'w', encoding='ascii', newline='\n') as f:
        f.write('\n'.join(out) + '\n')


def read_as(path):
    """Mimics the DBTool reader: strip whitespace, one block, key:value; fields."""
    txt = re.sub(r'\s+', '', open(path, encoding='ascii').read())
    for head in ('nphoneartsegmentation{', 'articulationasrsegmentation{'):
        if txt.startswith(head):
            break
    else:
        raise ValueError('%s: not recognized file format' % path)
    end = txt.find('};')
    if end < 0:
        raise ValueError('%s: no valid block end' % path)
    if txt[end + 2:]:
        raise ValueError('%s: trailing data after block end' % path)
    fields = {}
    for item in txt[len(head):end].split(';'):
        if not item:
            continue
        if ':' not in item:
            raise ValueError('%s: no key-value separator in %r' % (path, item))
        k, v = item.split(':', 1)
        if k in fields:
            raise ValueError('%s: key %s twice' % (path, k))
        fields[k] = v

    def lst(v):
        v = v.strip('[]')
        return [x for x in v.split(',') if x] if v else []

    if head.startswith('nphone'):
        phns = [x.strip('"') for x in lst(fields['phns'])]
        a = ArtSeg(phns, int(fields['cutoffset']), int(fields['cutlength']),
                   [float(x) for x in lst(fields['boundaries'])],
                   [x == 'true' for x in lst(fields['voiced'])], fields['revised'] == 'true')
    else:
        first, last = fields['firstphoneme'].strip('"'), fields['lastphoneme'].strip('"')
        b = [float(fields['beginmarktime']), float(fields['notealignmarktime']), float(fields['endmarktime'])]
        lv, rv = fields['leftregionvoiced'] == 'true', fields['rightregionvoiced'] == 'true'
        if first == last and b[1] < 0 and lv == rv:
            a = ArtSeg([first], int(fields['cutoffset']), int(fields['cutlength']), [b[0], b[2]], [lv],
                       fields['revised'] == 'true')
        else:
            a = ArtSeg([first, last], int(fields['cutoffset']), int(fields['cutlength']), b, [lv, rv],
                       fields['revised'] == 'true')
    n = len(a.phns)
    if len(a.boundaries) != (2 if n == 1 else 2 * n - 1):
        raise ValueError('%s: invalid number of boundaries for number of phonemes' % path)
    if len(a.voiced) != (1 if n == 1 else 2 * n - 2):
        raise ValueError('%s: invalid number of voiced flags for number of phonemes' % path)
    return a


# ---------------------------------------------------------------- whole-folder validation

def validate_folder(folder, dictionary=None, sample_rate=44100):
    """Check every .trans in folder the way the DBTool would. Returns list of error strings."""
    import os
    import wave
    errors = []
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith('.trans'):
            continue
        base = os.path.join(folder, fn[:-6])
        try:
            phonemes, directives = read_trans(base + '.trans')
            if dictionary is not None:
                bad = [p for p in phonemes if p not in dictionary]
                if bad:
                    raise ValueError('phonemes not in dictionary: %s' % '  '.join(bad))
            kinds = {len(u) == 1 for u, _ in directives}
            if len(kinds) > 1:
                raise ValueError('mixes stationaries and articulations')
            with wave.open(base + '.wav') as w:
                if w.getframerate() != sample_rate:
                    raise ValueError('sample rate %d != %d' % (w.getframerate(), sample_rate))
                nsamp = w.getnframes()
            dur = nsamp / sample_rate
            segs, stat, _ = read_seg(base + '.seg')
            if [s[0] for s in segs] != phonemes:
                raise ValueError('seg phonemes %s != trans %s' % ([s[0] for s in segs], phonemes))
            if stat != (True in kinds):
                raise ValueError('articulationsAreStationaries flag mismatch')
            for i, (_, b, e) in enumerate(segs):
                if b < 0 or e <= b:
                    raise ValueError('bad segment %d' % i)
                if i and abs(segs[i - 1][2] - b) > 1e-6:
                    raise ValueError('gap/overlap before segment %d' % i)
            if segs[-1][2] > dur + 1e-3:
                raise ValueError('segmentation past end of wav')
            for k, (unit, occ) in enumerate(directives):
                start = find_occurrence(phonemes, unit, occ)
                if start < 0:
                    raise ValueError('directive %s not in transcription' % unit)
                a = read_as('%s.as%d' % (base, k))
                if a.phns != unit:
                    raise ValueError('.as%d phonemes %s != directive %s' % (k, a.phns, unit))
                if a.cut_offset < 0 or a.cut_offset + a.cut_length > nsamp:
                    raise ValueError('.as%d cut region outside wav' % k)
                t = [a.cut_offset / sample_rate + x for x in a.boundaries]
                if any(t[i + 1] <= t[i] for i in range(len(t) - 1)):
                    raise ValueError('.as%d boundaries not increasing' % k)
                if t[0] < 0 or t[-1] > (a.cut_offset + a.cut_length) / sample_rate + 1e-6:
                    raise ValueError('.as%d boundaries outside cut region' % k)
                # marks must sit in the right phoneme segments
                segu = segs[start:start + len(unit)]
                if len(unit) == 1:
                    if not (segu[0][1] - 1e-6 <= t[0] and t[1] <= segu[0][2] + 1e-6):
                        raise ValueError('.as%d stationary marks outside segment' % k)
                else:
                    for j in range(len(unit)):
                        if not (segu[j][1] - 1e-6 <= t[2 * j] <= segu[j][2] + 1e-6):
                            raise ValueError('.as%d mark %d outside phoneme %s' % (k, 2 * j, unit[j]))
                    for j in range(len(unit) - 1):
                        if abs(t[2 * j + 1] - segu[j][2]) > 0.002:
                            raise ValueError('.as%d transition %d != seg boundary' % (k, j))
        except Exception as ex:  # noqa: BLE001 - collect all problems
            errors.append('%s: %s' % (fn, ex))
    return errors
