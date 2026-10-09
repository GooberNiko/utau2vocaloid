"""UTAU voicebank reading: oto.ini (any folder depth), prefix.map, pitch suffixes."""
import os
import re
import unicodedata
from dataclasses import dataclass

NOTE_RE = re.compile(r'(?:_?[A-G][#b]?-?\d)$')
NOTE_ANY = re.compile(r'(?<![A-Z#])([A-G][#b]?[1-8])(?![0-9])')


def read_text(path):
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'cp932', 'gbk', 'latin-1'):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return raw.decode('latin-1', 'replace'), 'latin-1'


@dataclass
class OtoEntry:
    wav: str            # absolute path
    alias: str          # alias with pitch suffix/prefix stripped
    raw_alias: str
    group: str          # pitch / voice-colour group (sub folder + suffix)
    dup: int            # trailing duplicate number ("a ka2" -> 2), 0 if none
    offset: float       # all times in seconds, absolute in the wav
    consonant: float
    cutoff_raw: float   # ms as written (negative = relative to offset)
    preutter: float
    overlap: float
    pitch: str = ''     # recording pitch from the folder/suffix ("C3", "A#3"), '' if unknown
    color: str = ''     # voice colour label: group with the pitch taken out ("Soft/SF", "Standard")
    full: str = ''      # alias with only the folder tag removed: "b3", "D3" are phonemes in X-SAMPA banks

    def end(self, wav_dur):
        """Absolute end time in seconds."""
        c = self.cutoff_raw / 1000.0
        if c < 0:
            return min(wav_dur, self.offset - c)
        return max(self.offset, wav_dur - c)


def read_prefix_map(bank_root):
    """Return the set of (prefix, suffix) pairs in prefix.map files (they are per note)."""
    pairs = set()
    for dirpath, _, files in os.walk(bank_root):
        for fn in files:
            if fn.lower() == 'prefix.map':
                txt, _ = read_text(os.path.join(dirpath, fn))
                for line in txt.splitlines():
                    parts = line.split('\t')
                    if len(parts) >= 3:
                        pre, suf = parts[1].strip(), parts[2].strip()
                        if pre or suf:
                            pairs.add((pre, suf))
    return pairs


def strip_affixes(alias, affixes, strip_notes=True):
    """Remove a prefix.map prefix/suffix (longest first) or a trailing note name like C4 / _F#3."""
    tag = ''
    for pre, suf in sorted(affixes, key=lambda p: -len(p[0]) - len(p[1])):
        if (not pre or alias.startswith(pre)) and (not suf or alias.endswith(suf)) and \
                len(alias) > len(pre) + len(suf):
            alias = alias[len(pre):len(alias) - len(suf) if suf else None]
            return alias, (pre + suf)
    if strip_notes:
        m = NOTE_RE.search(alias)
        if m and m.start() > 0:
            tag = m.group(0).lstrip('_')
            alias = alias[:m.start()]
    return alias, tag


def common_suffix(aliases, share=0.9):
    """Trailing string (pitch/colour tag like "C3", "C3SF", "_強") shared by nearly all aliases of one oto.ini."""
    best = ''
    for k in range(1, 9):
        tails = [a[-k:] for a in aliases if len(a) > k]
        if not tails:
            break
        s = max(set(tails), key=tails.count)
        if tails.count(s) < share * len(aliases):
            break
        best = s
    # a lone kana/vowel shared by everything is content, not a tag
    if re.fullmatch(r'[぀-ヿー\s]*|[a-z]', best):
        return ''
    return best.lstrip() if best.strip() else ''


def split_label(label):
    """"SoftC3/C3SF" -> ("C3", "Soft/SF"): the first note name is the pitch, the rest the colour."""
    m = NOTE_ANY.search(label)
    if not m:
        return '', label.strip(' _-/')
    pitch = m.group(1)
    color = NOTE_ANY.sub('', label)
    color = '/'.join(p.strip(' _-') for p in color.split('/') if p.strip(' _-'))
    return pitch, color


def read_bank(bank_root, extra_suffixes=()):
    """Collect every oto.ini entry under bank_root."""
    affixes = read_prefix_map(bank_root) | {('', s) for s in extra_suffixes}
    pitch_tags = {pre + suf for pre, suf in affixes if pre + suf}
    entries = []
    for dirpath, _, files in os.walk(bank_root):
        for fn in files:
            if fn.lower() != 'oto.ini':
                continue
            sub = os.path.relpath(dirpath, bank_root)
            sub = '' if sub == '.' else sub.replace('\\', '/')
            txt, _ = read_text(os.path.join(dirpath, fn))
            # banks zipped on a Mac store "が" as か + combining dakuten: match file names normalized
            on_disk = {unicodedata.normalize('NFC', f): f for f in files}
            rows = []
            for line in txt.splitlines():
                if '=' not in line:
                    continue
                wav, rest = line.split('=', 1)
                parts = rest.split(',')
                if len(parts) < 6:
                    continue
                raw_alias = parts[0].strip() or os.path.splitext(wav)[0]
                try:
                    vals = tuple(float(x or 0) for x in parts[1:6])
                except ValueError:
                    continue
                rows.append((wav, raw_alias, vals))
            folder_tag = common_suffix([r[1] for r in rows])
            for wav, raw_alias, (off, con, cut, pre, ovl) in rows:
                if folder_tag and raw_alias.endswith(folder_tag) and len(raw_alias) > len(folder_tag):
                    alias, tag = raw_alias[:-len(folder_tag)], folder_tag
                    full = alias
                else:
                    alias, tag = strip_affixes(raw_alias, affixes)
                    full = raw_alias
                dup = 0
                m = re.search(r'(?<=[^\s\d])(\d+)$', alias)
                if m:
                    dup = int(m.group(1))
                    alias = alias[:m.start()]
                group = '/'.join(x for x in (sub, tag) if x) or 'main'
                # a suffix prefix.map assigns to notes is a pitch tag ("F" for the high folder), not a colour
                label = sub if tag and tag in pitch_tags else group
                pitch, color = split_label(label if label != 'main' else '')
                if not pitch and tag in pitch_tags:
                    pitch = tag
                path = os.path.join(dirpath, on_disk.get(unicodedata.normalize('NFC', wav), wav))
                entries.append(OtoEntry(path, alias.strip(), raw_alias, group, dup,
                                        off / 1000.0, (off + con) / 1000.0, cut, (off + pre) / 1000.0,
                                        (off + ovl) / 1000.0, pitch, color, full.strip()))
    return entries
