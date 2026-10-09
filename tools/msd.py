"""Read the unit timeline from a VOCALOID4 Editor for Developer .msd file (written next to an exported wav).

    python -I tools/msd.py song.msd          -> one line per record: begin end phoneme [unit]

Layout (reverse-engineered): 'MSDX', u32 version, u32 record count, ...; each record starts with
u32 frame (hop 256 at 44.1 kHz), u32 frames, f32 begin s, f32 end s, char phoneme[32], ... and the two
phonemes of the unit in use at +0x6d and +0xad (64 bytes apart). A stationary appears as [o o].
"""
import re
import struct
import sys

PHONEMES = set(r"a i M e o N\ N N' J m m' n k k' g g' s S z Z dz dZ t t' d d' ts tS h C p\ p\' b b' p p' "
               r"4 4' j w Sil ? Asp".split()
               + r"I e { Q V U @ i: u: O: @r eI aI OI @U aU I@ e@ U@ O@ Q@ @l f T v D l r R".split())


def read_msd(path):
    d = open(path, 'rb').read()
    if d[:4] != b'MSDX':
        raise ValueError('not an .msd file: %s' % path)
    hits = [(m.start(), m.group(1).decode('latin-1')) for m in re.finditer(rb'(?<=\x00)([ -~]{1,8})\x00', d)]
    hits = [(o, p) for o, p in hits if p.split('#')[0] in PHONEMES]
    recs, i = [], 0
    while i < len(hits) - 1:
        (o, a), (o2, b) = hits[i], hits[i + 1]
        if o2 - o == 64 and o - 0x6d >= 0:
            s = o - 0x6d
            fr, nf, t0, t1 = struct.unpack('<2I2f', d[s:s + 16])
            name = d[s + 16:s + 48].split(b'\0')[0].decode('latin-1')
            if 0 <= t0 <= t1 < 36000:
                recs.append((t0, t1, name, (a, b)))
            i += 2
        else:
            i += 1
    return recs


if __name__ == '__main__':
    for t0, t1, name, (a, b) in read_msd(sys.argv[1]):
        print('%8.3f %8.3f  %-4s [%s %s]' % (t0, t1, name, a, b))
