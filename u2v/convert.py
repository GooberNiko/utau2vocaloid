"""UTAU (VCV / CVVC / CV, Japanese or English Arpasing) -> VOCALOID3 DBTool segmentation folder."""
import csv
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from . import audio, dbtool_io, oto, phonemes as P

TARGET_SR = 44100
MIN_SEG = 0.012


@dataclass
class Clip:
    entry: oto.OtoEntry
    phns: list
    start: float                 # absolute seconds in the source wav
    end: float
    bounds: list                 # absolute transition times, len(phns)-1
    detected: list               # per boundary: placed by acoustics (True) or oto fallback (False)
    derived: str = ''            # how the clip was made when it is not a plain oto entry
    mute_before: float = None    # absolute time; audio before it is replaced by silence
    mute_after: float = None     # absolute time; audio after it is faded out and replaced by silence
    audio: object = None         # spliced clips: their own samples (start 0), not cut from entry.wav
    units: list = field(default_factory=list)    # selected diphone indices

    def segments(self):
        t = [self.start] + self.bounds + [self.end]
        return [(p, t[i], t[i + 1]) for i, p in enumerate(self.phns)]


class Converter:
    def __init__(self, bank, out, lang='auto', dict_path=None, groups=None, keep=1, derive=True,
                 merge=False, fill=True, log=print, evec=True, splice=True, extend_dict=True,
                 normalize=True, colors='main', brighten=None, avoid=(), extras=True):
        self.bank, self.out, self.keep, self.derive, self.log = bank, out, keep, derive, log
        self.fill, self.splice, self.normalize = fill, splice, normalize
        self.brighten = brighten or {}                       # {phoneme: dB of treble above 2 kHz}
        self.avoid = {tuple(u.split()) for u in avoid}       # units not to take from recordings
        self.colors = 'merge' if merge else colors          # main | evec | merge
        self.evec = evec and self.colors == 'evec'
        self.extras = extras
        self.entries = oto.read_bank(bank)
        if not self.entries:
            raise SystemExit('no oto.ini entries found under %s' % bank)
        for e in self.entries:
            e.extra_folder = bool(P.EXTRA_FOLDER.search(e.group))
        all_groups = sorted({e.group for e in self.entries})
        self.log('folders/suffixes: %s' % ', '.join(all_groups))
        if groups:
            rx = re.compile(groups)
            # extras (breaths, rolled r ...) are used from their bonus folder even when --groups picks
            # only the singing folders
            self.entries = [e for e in self.entries if rx.search(e.group)
                            or (extras and e.extra_folder and self._extra(e))]
            if not self.entries:
                raise SystemExit('--groups %r matches none of: %s' % (groups, ', '.join(all_groups)))
            self.log('using: %s' % ', '.join(sorted({e.group for e in self.entries})))
        self.assign_colors(merge)
        self.lang = P.detect_language([e.alias for e in self.entries]) if lang == 'auto' else lang
        self.log('language: %s, %d oto entries' % (self.lang, len(self.entries)))
        self.dict = dbtool_io.load_dictionary(dict_path) if dict_path else None
        if self.dict is not None and extend_dict and self.lang == 'ja':
            self.dict = dbtool_io.extend_dictionary(self.dict, P.JA_EXTRA)
        self.extras_found = sorted({p for e in self.entries for p in (self._extra(e) or []) if p in P.EXTRAS})
        if self.dict is not None and self.extras_found:
            self.dict = dbtool_io.extend_dictionary(self.dict, {p: P.EXTRA_DICT[p] for p in self.extras_found})
        self.skipped = []          # (alias, wav, reason)
        self._audio = {}

    # -------------------------------------------------------------- voice colours and pitches
    def assign_colors(self, merge):
        """Split the bank into voice colours (Soft, Whisper, ...) and pitches (C3, F#3, ...).

        Every pitch of one colour shares phoneme names, so the DB holds several pitches per unit and
        VOCALOID picks the closest. Colours other than the main one get an EVEC-style suffix:
        "a#1", "k'#1", ... (Sil stays Sil). Units are chosen per colour and pitch."""
        for e in self.entries:
            if merge:
                e.color, e.pitch = 'merged', ''
            e.color = P.NUMBERING.sub(r'\1', P.METHOD_WORDS.sub('', e.color)).strip(' _-/.') or 'main'
        size = defaultdict(int)
        for e in self.entries:
            size[e.color] += 1
        big = max(size.values())
        keep = [c for c in size if size[c] >= 0.1 * big]
        cp = os.path.commonprefix(keep) if len(keep) > 1 else ''

        def main_like(c):
            return c[len(cp):] == '' or bool(P.MAIN_COLOR.search(c))
        default = max(keep, key=lambda c: (main_like(c), size[c]))
        merge_to = {}
        for c in size:                          # a few stray entries join the colour they belong to
            if c not in keep:
                merge_to[c] = next((k for k in sorted(keep, key=lambda k: -size[k])
                                    if k.startswith(c) or c.startswith(k)), default)
        order = [default] + sorted((k for k in keep if k != default), key=lambda k: (-size[k], k))
        self.color_suffix = {k: '#%d' % i if i and self.evec else '' for i, k in enumerate(order)}
        for e in self.entries:
            e.color = merge_to.get(e.color, e.color)
        if self.colors == 'main' and len(order) > 1:
            # The DBTool reads "#" in a dictionary as a comment, so EVEC-style colours can't be built
            # yet; mixing colours under one phoneme set would make VOCALOID switch voice at random.
            self.log('only the main colour is converted (%s); others: %s  (--colors merge|evec)'
                     % (default, ', '.join(order[1:])))
            self.entries = [e for e in self.entries if e.color == default]
            order = [default]
        # extras recorded without a pitch (bonus folders) join that colour's most common pitch
        common = {}
        for k in order:
            ps = [e.pitch for e in self.entries if e.color == k and e.pitch]
            common[k] = max(set(ps), key=ps.count) if ps else ''
        for e in self.entries:
            if not e.pitch:
                e.pitch = common.get(e.color, '')
            e.group = e.color + ('@' + e.pitch if e.pitch else '')
        self.group_color = {e.group: e.color for e in self.entries}
        for k in order:
            pitches = sorted({e.pitch for e in self.entries if e.color == k and e.pitch})
            self.log('colour %-24s suffix %-4s pitches %s' % (k, self.color_suffix[k] or '-',
                                                              ' '.join(pitches) or '-'))

    def suffixer(self, group):
        sfx = self.color_suffix[self.group_color[group]]
        return lambda p: p if not sfx or p in P.SIL else p + sfx

    # -------------------------------------------------------------- audio cache
    def load(self, path):
        if path not in self._audio:
            x, sr = audio.read_wav(path)
            x = audio.resample(x, sr, TARGET_SR)
            self._audio[path] = (x, audio.Features(x, TARGET_SR))
        return self._audio[path]

    # -------------------------------------------------------------- phonemes
    def _extra(self, e):
        """Phonemes of an UTAU extra (breath, vowel ending, rolled r), or None."""
        if not self.extras:
            return None
        return P.extra_alias(e.alias + (str(e.dup) if e.dup else ''), getattr(e, 'extra_folder', False))

    def map_phonemes(self, e):
        ex = self._extra(e)
        if ex is not None:
            if self.dict is not None and any(p not in self.dict for p in ex):
                return None, 'extra phoneme not in DB dictionary'
            return ex, None
        ph = None
        if self.lang == 'en' and e.full and e.full != e.alias:
            ph = P.alias_to_phonemes(e.full, 'en')       # "b3" is b + 3 (bird), not "b" duplicate 3
        ph = ph or P.alias_to_phonemes(e.alias, self.lang)
        if ph is None:
            return None, 'alias not understood'
        if self.dict is not None:
            out = []
            for p in ph:
                if p not in self.dict and p in P.JA_FALLBACK and P.JA_FALLBACK[p] in self.dict:
                    p = P.JA_FALLBACK[p]
                if p not in self.dict:
                    return None, 'phoneme %r not in DB dictionary' % p
                out.append(p)
            ph = out
        return ph, None

    def voiced(self, ph):
        return self.dict.is_voiced(ph) if self.dict is not None else not P.is_unvoiced(ph)

    # -------------------------------------------------------------- segmentation
    def segment_extra(self, e, phs):
        """Extras are cut by level and voicing, not by oto timing: a breath / rolled r is the loud part
        between silences, a vowel ending is voiced until it stops, then the breath that follows."""
        x, ft = self.load(e.wav)
        dur = len(x) / TARGET_SR
        off, end = e.offset, e.end(dur)
        f0, f1 = ft.t2f(off), ft.t2f(end)
        if f1 - f0 < 10:
            return None, 'region too short'
        en = ft.energy[f0:f1 + 1]
        floor = float(np.percentile(ft.energy, 5))
        # a breath / trill file may have no silence at all: then "active" is relative to its own peak
        act = en > max(en.max() - 40, min(floor + 12, en.max() - 12))
        if not act.any():
            return None, 'no sound in the region'
        t = lambda k: ft.f2t(f0 + k)
        if phs[0] == 'Sil':                                    # Sil X Sil: breath, rolled r
            k0 = int(np.argmax(act))
            k1 = len(act) - 1 - int(np.argmax(act[::-1]))
            b0, b1 = max(off, t(k0) - 0.02), min(end, t(k1) + 0.02)
            if b1 - b0 < 0.05:
                return None, 'too short'
            # extras are often trimmed with no silence around them: pad with silence (cut() pads)
            return Clip(e, phs, b0 - 0.15, b1 + 0.15, [b0, b1], [True, True],
                        mute_before=b0, mute_after=b1), None
        v = ft.voiced[f0:f1 + 1]
        if not v.any():
            return None, 'no voiced vowel'
        kv = int(np.argmax(v))
        kv_end = kv
        while kv_end + 1 < len(v) and (v[kv_end + 1] or (kv_end + 2 < len(v) and v[kv_end + 2])):
            kv_end += 1
        tv = t(kv_end)                                         # the vowel stops here
        if tv - t(kv) < 0.1:
            return None, 'vowel too short'
        start = max(off, tv - 0.6)
        rest = act[kv_end + 1:]
        if phs[1:] == ['Sil']:                                 # a R: vowel ending
            return Clip(e, phs, start, min(dur, tv + 0.25), [tv], [True], mute_after=tv + 0.02), None
        if phs[1] == 'brE':                                    # a R息: the breath out follows at once
            k1 = kv_end + 1 + (len(rest) - 1 - int(np.argmax(rest[::-1])) if rest.any() else 0)
            be = max(t(k1), tv + 0.08)
            return Clip(e, phs, start, min(dur, be + 0.15), [tv, be], [True, bool(rest.any())],
                        mute_after=be), None
        # a R吸: vowel, pause, breath in
        gap = int(0.06 / audio.HOP)
        later = act[kv_end + 1 + gap:]
        if not later.any():
            return None, 'no breath after the vowel'
        k2 = kv_end + 1 + gap + int(np.argmax(later))
        k3 = len(act) - 1 - int(np.argmax(act[::-1]))
        b2, b3 = t(k2) - 0.01, t(k3) + 0.01
        if b3 - b2 < 0.08:
            return None, 'breath too short'
        return Clip(e, phs, start, min(dur, b3 + 0.15), [tv, b2, b3], [True, True, True], mute_after=b3), None

    def segment(self, e, phs):
        if any(p in P.EXTRAS for p in phs) or self._extra(e) is not None:
            return self.segment_extra(e, phs)
        x, ft = self.load(e.wav)
        dur = len(x) / TARGET_SR
        off, end = e.offset, e.end(dur)
        if end - off < 0.05:
            return None, 'region too short'
        pre = min(max(e.preutter, off + MIN_SEG), end - MIN_SEG)
        n = len(phs)
        cls = [P.pclass(p) for p in phs]
        bounds, det = [None] * (n - 1), [False] * (n - 1)
        if n >= 2:
            a = next((i for i in range(1, n) if phs[i] in P.SUSTAINED), 1)
            lo, hi = max(off + MIN_SEG, pre - 0.12), min(end - MIN_SEG, pre + 0.12)
            bounds[a - 1], det[a - 1] = audio.find_boundary(ft, cls[a - 1], cls[a], pre, lo, hi, phs[a])
            for j in range(a - 2, -1, -1):          # leftwards
                nxt = bounds[j + 1]
                typ = P.TYPICAL_DUR.get(phs[j + 1], 0.08)
                floor = max(0.0, off - 0.15) if phs[j] == 'Sil' else off + 0.005
                lo, hi = max(floor, nxt - 3 * typ - 0.05), nxt - MIN_SEG
                if phs[j] == 'Sil' and hi - lo < 0.01:
                    # sample trimmed right at the consonant: the closure/silence is padded in later
                    bounds[j], det[j] = nxt - typ, False
                else:
                    bounds[j], det[j] = audio.find_boundary(ft, cls[j], cls[j + 1], nxt - typ, lo, hi, phs[j + 1])
            for j in range(a, n - 1):               # rightwards
                prv = bounds[j - 1]
                typ = max(P.TYPICAL_DUR.get(phs[j], 0.08), 0.08)
                lo, hi = prv + MIN_SEG, min(end - 0.005, prv + 3 * typ + 0.1)
                bounds[j], det[j] = audio.find_boundary(ft, cls[j], cls[j + 1], prv + typ, lo, hi, phs[j + 1])
        start, stop = off, end
        # oto regions often clip the vowels at either end of a unit (a CVVC "a k" starts just before the
        # consonant): take more of the vowel from the recording, up to the neighbouring syllable
        if n >= 2 and phs[0] in P.SUSTAINED and bounds[0] - start < 0.25:
            s0 = audio.sustained_extent(ft, bounds[0] - 0.05, backward=True)[0]
            start = max(0.0, min(start, s0), bounds[0] - 0.4)
        if n >= 2 and phs[-1] in P.SUSTAINED and stop - bounds[-1] < 0.25:
            s1 = audio.sustained_extent(ft, bounds[-1] + 0.04)[1]
            stop = min(dur, max(stop, s1), bounds[-1] + 0.4)
        if phs[0] == 'Sil' and n >= 2:              # may go below 0: padded with dither when written
            start = min(off, bounds[0] - 0.10)
        if phs[-1] == 'Sil' and n >= 2:
            stop = max(end, bounds[-1] + 0.10)
        t = [start] + bounds + [stop]
        if any(t[i + 1] - t[i] < MIN_SEG for i in range(len(t) - 1)):
            return None, 'boundaries collapsed (oto values inconsistent?)'
        return Clip(e, list(phs), start, stop, bounds, det), None

    def derive_silence(self, c):
        """Find real silence just before/after the oto region -> extra [Sil X] / [X Sil] clips."""
        x, ft = self.load(c.entry.wav)
        dur = len(x) / TARGET_SR
        out = []
        # Vowel phrase starts are not taken from the start of a recording string: rendered in the dev
        # editor they fade in slowly (Teto's "- o": 200 ms), while a [Sil V] made from the steady vowel
        # attacks in 10-15 ms. Consonant starts are fine as recorded.
        if c.phns[0] != 'Sil' and c.phns[0] not in P.SUSTAINED:
            b0 = c.bounds[0] if c.bounds else c.end
            f_on = ft.t2f(c.start + 0.03)
            lo = ft.t2f(max(0.0, c.start - 0.25))
            if c.phns[0] in P.SUSTAINED:
                # a VCV entry starts inside a vowel that may have been held for a while since its
                # attack: follow the same vowel back (never across a consonant) before looking for silence
                s0 = audio.sustained_extent(ft, c.start + 0.05, backward=True, max_dist=3.0, max_drop=12)[0]
                f_on = min(f_on, ft.t2f(s0))
                lo = ft.t2f(max(0.0, s0 - 0.25))
            f = f_on
            while f > lo and not ft.silent(f):
                f -= 1
            sil = 0
            g = f
            while g > max(0, f - 60) and ft.silent(g):
                sil += 1
                g -= 1
            vowel_first = c.phns[0] in P.SUSTAINED
            # A vowel only starts a phrase after real silence and with a voiced attack. A shorter
            # silence is a plosive closure inside the string, and the "vowel" would start with its burst
            # (an [Sil a] that sings "ka").
            need = 40 if vowel_first else 16
            if g <= 0 and sil >= 10:              # silence reaching the start of the file counts as a phrase start
                need = 10
            clean = not vowel_first or (f + 8 < ft.n and ft.voiced[f + 4:f + 8].all()
                                        and (ft.hf_ratio[f + 1:f + 6] < 0).all())
            on = None
            if ft.silent(f) and sil >= need and f > 0 and clean:
                on = ft.f2t(f + 1) - audio.HOP / 2
            elif c.entry.offset < 0.1 and ft.energy[0] < np.percentile(ft.energy, 95) - 15:
                on = 0.0                         # trimmed sample that starts from silence
            if on is not None:
                if P.pclass(c.phns[0]) == 'uplos':
                    on -= 0.02
                first = P.JA_INITIAL.get(c.phns[0], c.phns[0]) if self.lang == 'ja' else c.phns[0]
                if MIN_SEG < b0 - on and (self.dict is None or first in self.dict):
                    out.append(Clip(c.entry, ['Sil', first] + c.phns[1:], on - 0.12, c.end,
                                    [on] + c.bounds, [True] + c.detected, 'lead-sil'))
        if c.phns[-1] != 'Sil' and c.phns[-1] in P.SUSTAINED:
            last = c.bounds[-1] if c.bounds else c.start
            f = ft.t2f(c.end - 0.02)
            hi = ft.t2f(min(dur, c.end + 0.4))
            while f < hi and not ft.silent(f):
                f += 1
            run = 0
            g = f
            while g < ft.n - 1 and ft.silent(g) and run < 20:
                run += 1
                g += 1
            if f < hi and run >= 16:
                off_t = ft.f2t(f) - audio.HOP / 2
                if off_t - last > 0.06:
                    out.append(Clip(c.entry, c.phns + ['Sil'], c.start, min(dur, off_t + 0.10),
                                    c.bounds + [off_t], c.detected + [True], 'tail-sil'))
        return out

    # -------------------------------------------------------------- filling gaps
    def fill_missing(self, clips, cands, chosen):
        """Make units the bank lacks from close relatives: relabelled closures, muted onsets."""
        exp = self.expected_units()
        if not exp:
            return
        groups = {c.entry.group for c in clips}
        for g in sorted(groups):
            have = {(k[1], k[2]) for k in cands if k[0] == g}
            for X, Y in sorted(exp - have):
                made = None
                if X == 'Sil' and Y not in P.VOWELS:
                    made = self._synth_onset(clips, cands, g, Y)
                elif X == 'Sil' and Y in P.SUSTAINED:
                    made = self._synth_sustained(clips, g, Y, 'onset')
                elif Y == 'Sil' and X in P.SUSTAINED:
                    made = self._synth_sustained(clips, g, X, 'tail')
                if made is None:
                    made = self._relabel(clips, cands, g, X, Y)
                if made is None and self.splice:
                    made = self._splice(clips, cands, g, X, Y) or self._splice_cv(clips, cands, g, X, Y)
                if made is not None:
                    clips.append(made)
                    i = next(j for j in range(len(made.phns) - 1) if made.phns[j:j + 2] == [X, Y])
                    chosen[len(clips) - 1].append(i)
                    cands[(g, X, Y)].append((self.unit_score(made, i), len(clips) - 1, i))
            # extras: let breaths and the rolled r follow or lead into any sung vowel without a rest
            if self.extras and self.splice:
                here = {p for (gg, a, b) in cands if gg == g for p in (a, b) if p in P.EXTRAS}
                want = set()
                onsets = sorted({k[2] for k in cands if k[0] == g and k[1] == 'Sil'
                                 and k[2] not in P.VOWELS and k[2] not in P.EXTRAS})
                for x in here:
                    want |= {(v, x) for v in self.VOWELS_JA + ['N\\']}
                    if x != 'brE':                       # brE ends a phrase; the others lead into singing
                        want |= {(x, v) for v in self.VOWELS_JA}
                    if x in P.BREATHS and x != 'brE':    # a breath, then straight into "sa", "ka" ...
                        want |= {(x, c) for c in onsets}
                have = {(k[1], k[2]) for k in cands if k[0] == g}
                for X, Y in sorted(want - have):
                    if Y in P.VOWELS or X in P.VOWELS or 'N\\' in (X, Y):
                        made = self._splice_extra(clips, g, X, Y)
                    else:
                        made = self._breath_onset(clips, cands, g, X, Y)
                    if made is not None:
                        clips.append(made)
                        i = made.phns.index(X)
                        chosen[len(clips) - 1].append(i)
                        cands[(g, X, Y)].append((self.unit_score(made, i), len(clips) - 1, i))

    VOWELS_JA = ['a', 'i', 'M', 'e', 'o']

    def _extra_take(self, clips, g, ph):
        """The recorded extra ph in group g: (clip, begin, end) of its segment, the longest one."""
        best = None
        for c in clips:
            if c.derived or c.audio is not None or c.entry.group != g:
                continue
            for p, b, e in c.segments():
                if p == ph and (best is None or e - b > best[2] - best[1]):
                    best = (c, b, e)
        return best

    def _splice_extra(self, clips, g, X, Y):
        """[V extra] / [extra V]: a sung vowel joined to a breath or rolled r recorded on its own. A
        breath starts as the vowel stops (fade out, then the breath); a rolled r crossfades."""
        sr = TARGET_SR
        ex, vo = (Y, X) if Y in P.EXTRAS else (X, Y)
        t_ex = self._extra_take(clips, g, ex)
        t_vo = self._long_segment(clips, g, vo)
        if t_ex is None or t_vo is None:
            return None
        ce, eb, ee = t_ex
        cv, vb, ve = t_vo
        xe_, _ = self.load(ce.entry.wav)
        xv_, _ = self.load(cv.entry.wav)
        steady = vb + min(0.06, 0.25 * (ve - vb))
        L = min(0.3, ve - 0.02 - steady)
        if L < 0.08:
            return None
        vow = audio.cut(xv_, int(steady * sr), int((steady + L) * sr))
        voiced = ex in P.TRILL
        f = int((0.02 if voiced else 0.012) * sr)
        if vo == X:                                              # [V extra]: vowel, then the extra
            ext = audio.cut(xe_, int(eb * sr), int((ee + 0.12) * sr))
            if voiced:
                w = np.linspace(0, 1, f)
                y = np.concatenate([vow[:-f], vow[-f:] * (1 - w) + ext[:f] * w, ext[f:]])
                T = len(vow) / sr - f / sr
            else:
                vow[-f:] *= np.linspace(1, 0, f)
                ext[:int(0.005 * sr)] *= np.linspace(0, 1, int(0.005 * sr))
                y = np.concatenate([vow, ext])
                T = len(vow) / sr
            bounds = [T, T + (ee - eb)]
            phns = [X, Y, 'Sil']
        else:                                                    # [extra V]: the extra, then the vowel
            pre = 0.12
            ext = audio.cut(xe_, int((eb - pre) * sr), int(ee * sr))
            ext[:int(pre * sr)] = audio.cut(xe_, -int(pre * sr) - 10, -10)
            if voiced:
                w = np.linspace(0, 1, f)
                y = np.concatenate([ext[:-f], ext[-f:] * (1 - w) + vow[:f] * w, vow[f:]])
                T2 = len(ext) / sr - f / sr
            else:
                ext[-f:] *= np.linspace(1, 0, f)
                n = int(0.025 * sr)
                vow[:n] *= 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))
                y = np.concatenate([ext, vow])
                T2 = len(ext) / sr
            bounds = [pre, T2]
            phns = ['Sil', X, Y]
        return Clip(cv.entry, phns, 0.0, len(y) / sr, bounds, [False] * len(bounds),
                    'splice %s+%s' % (X, Y), audio=y.astype(np.float32))

    def _breath_onset(self, clips, cands, g, B, C):
        """[breath C]: the breath, then the bank's own [Sil C] from the consonant on."""
        t_b = self._extra_take(clips, g, B)
        r = self._best(cands, g, 'Sil', C)
        if t_b is None or r is None:
            return None
        sr = TARGET_SR
        cb_, bb, be = t_b
        c = clips[r[1]]
        xb, _ = self.load(cb_.entry.wav)
        pre = 0.12
        br = audio.cut(xb, int((bb - pre) * sr), int(be * sr))
        br[:int(pre * sr)] = audio.cut(xb, -int(pre * sr) - 10, -10)
        f = int(0.01 * sr)
        br[-f:] *= np.linspace(1, 0, f)
        segs = c.segments()
        cs = segs[1][1]                                       # where C starts in its clip
        if c.audio is not None:
            rest = c.audio[int((cs - c.start) * sr):int((c.end - c.start) * sr)].copy()
        else:
            rest = audio.cut(self.load(c.entry.wav)[0], int(cs * sr), int(c.end * sr))
        y = np.concatenate([br, rest]).astype(np.float32)
        T = len(br) / sr
        bounds = [pre, T] + [T + (b - cs) for b in c.bounds[1:]]
        return Clip(c.entry, ['Sil', B] + list(c.phns[1:]), 0.0, len(y) / sr, bounds,
                    [False] * len(bounds), 'splice %s+%s' % (B, C), audio=y)

    def _best(self, cands, g, X, Y, clips=None):
        """Best candidate for [X Y]; with clips given, only units cut from a single recording."""
        lst = [r for r in cands.get((g, X, Y), []) if clips is None or clips[r[1]].audio is None]
        if not lst:
            return None
        return max(lst, key=lambda r: r[0])

    def _relabel(self, clips, cands, g, X, Y):
        if Y in P.VOWELS and X not in P.VOWELS and X not in P.NASAL and X != 'Sil':   # [C V]
            xs, ys = [X] + P.SUBST_CV.get(X, []), [Y]
        else:
            xs = [X] + (P.SUBST.get(X, []) if X in P.NASAL else [])
            ys = [Y] + (P.SUBST.get(Y, []) if Y not in P.VOWELS else [])
        for x2 in xs:
            for y2 in ys:
                if (x2, y2) == (X, Y):
                    continue
                r = self._best(cands, g, x2, y2, clips)
                if r is None:
                    continue
                c = clips[r[1]]
                i = r[2]
                phns = list(c.phns)
                phns[i], phns[i + 1] = X, Y
                return Clip(c.entry, phns, c.start, c.end, list(c.bounds), list(c.detected),
                            'subst %s %s<-%s %s' % (X, Y, x2, y2), c.mute_before, c.mute_after)
        return None

    def _synth_onset(self, clips, cands, g, C):
        """[Sil C]: take C from a CV/VCV clip and silence everything before it."""
        best = None
        for c2 in [C] + P.SUBST_CV.get(C, []):
            for (gg, a, b), lst in cands.items():
                if gg != g or a != c2 or b not in P.VOWELS:
                    continue
                for sc, ci, i in lst:
                    if clips[ci].derived:
                        continue
                    if best is None or sc > best[0]:
                        best = (sc, ci, i, c2)
        if best is None:
            return None
        _, ci, i, c2 = best
        c = clips[ci]
        segs = c.segments()
        cb, ce = segs[i][1], segs[i][2]
        ve = segs[i + 1][2]
        return Clip(c.entry, ['Sil', C, c.phns[i + 1]], cb - 0.12, ve, [cb, ce],
                    [False, c.detected[i]], 'muted onset from %s %s' % (c2, c.phns[i + 1]), cb)

    def _synth_sustained(self, clips, g, V, side):
        """[Sil V] / [V Sil] for banks without real silence: fade a long V in from or out to silence."""
        best = self._long_segment(clips, g, V)      # the same typical take the splices use
        if best is None:
            return None
        c, b, e = best
        if side == 'onset':
            t = b + min(0.06, 0.25 * (e - b))
            return Clip(c.entry, ['Sil', V], t - 0.15, min(e, t + 0.35), [t], [False],
                        'faded onset of %s' % V, mute_before=t)
        t = e - min(0.04, 0.2 * (e - b))
        return Clip(c.entry, [V, 'Sil'], max(b + 0.02, t - 0.35), t + 0.15, [t], [False],
                    'faded tail of %s' % V, mute_after=t)

    def _long_segment(self, clips, g, ph, min_len=0.09):
        """A long, typical-sounding recorded stretch of ph in group g: (clip, begin, end) or None."""
        ref, md = getattr(self, 'typical', {}).get((g, ph), (None, 1.0))
        best, best_s = None, None
        for c in clips:
            if c.derived or c.entry.group != g or c.audio is not None:
                continue
            ft = self.load(c.entry.wav)[1] if ref is not None else None
            for p, b, e in c.segments():
                if p != ph:
                    continue
                if e - b < min_len and p in P.SUSTAINED:
                    # a CV bank's oto region can stop early: the sound itself may go on much longer
                    e = max(e, audio.sustained_extent(self.load(c.entry.wav)[1], b + 0.03)[1])
                if e - b < min_len:
                    continue
                sc = min(e - b, 0.6)
                if ref is not None:
                    sc -= 0.3 * np.linalg.norm(ft.spec[ft.t2f(b + 0.04):ft.t2f(e - 0.03) + 1].mean(axis=0) - ref) / md
                if best_s is None or sc > best_s:
                    best, best_s = (c, b, e), sc
        return best

    def _cons_head(self, clips, cands, g, C):
        """Best recorded [C V] in group g: (clip, cons begin, cons end, vowel, vowel end) or None."""
        best = None
        alts = [C] + P.SUBST_CV.get(C, []) + [q for q in P.SUBST.get(C, []) if q not in P.SUSTAINED]
        for k, c2 in enumerate(alts):    # dz/dZ may only exist as z/Z, t'/d' as t/d in CV banks
            for (gg, a, b), lst in cands.items():
                if gg != g or a != c2 or b not in P.VOWELS:
                    continue
                for sc, ci, i in lst:
                    sc -= 2.0 * k
                    if not clips[ci].derived and (best is None or sc > best[0]):
                        best = (sc, clips[ci], i)
        if best is None:
            return None
        _, c, i = best
        segs = c.segments()
        return c, segs[i][1], segs[i][2], segs[i + 1][0], segs[i + 1][2]

    def _splice(self, clips, cands, g, X, Y):
        """Fake a transition the bank never recorded ([V C], [V V], [V N\\], [N C] in CV banks):
        the end of a long X joined to the start of Y taken from another sample."""
        xs = 'N\\' if X in P.NASAL else X               # ん context variants all come from ん
        if xs not in P.SUSTAINED or Y == 'Sil':
            return None
        src = self._long_segment(clips, g, xs)
        if src is None:
            return None
        cx, xb, xe = src
        # X from the start of its steady part, as the faded [Sil V] onsets and the CV splices are: a long
        # vowel drifts, and units cut from its start and its end would not match where they meet
        x_beg = xb + min(0.06, 0.25 * (xe - xb))
        L = min(0.3, xe - min(0.04, 0.2 * (xe - xb)) - x_beg)
        if L < 0.05:                                     # tiny CV banks: ん may be only ~0.09 s
            return None
        x_end = x_beg + L
        if Y in P.SUSTAINED:
            s = self._long_segment(clips, g, Y)
            if s is None:
                return None
            cy, yb, ye = s
            ya = yb + min(0.08, 0.3 * (ye - yb))          # past the attack, into the steady part
            yz, tail, ybounds = min(ye - 0.02, ya + 0.3), [], []
        else:
            h = self._cons_head(clips, cands, g, Y)
            if h is None:
                return None
            cy, ya, ce, v, ve = h
            yz, tail, ybounds = min(ve, ce + 0.15), [v], [ce]
        sr = TARGET_SR
        xa, _ = self.load(cx.entry.wav)
        ya_, _ = self.load(cy.entry.wav)
        x0, xt, n = int(round(x_beg * sr)), int(round(x_end * sr)), int(round(L * sr))
        if P.pclass(Y) in ('uplos', 'ufric'):
            # the vowel stops into the closure / frication: fade out, then the consonant as recorded
            head = audio.cut(xa, x0, xt)
            f = int(0.008 * sr)                       # full level up to the boundary, quick stop
            head[-f:] *= np.linspace(1, 0, f)
            rest = audio.cut(ya_, int(round(ya * sr)), int(round(yz * sr)))
            f = int(0.005 * sr)
            rest[:f] *= np.linspace(0, 1, f)
            y = np.concatenate([head, rest])
        else:
            # voiced join: linear crossfade (30 ms, 60 ms vowel to vowel), Y shifted by up to one period to line up phase
            ov = int((0.06 if Y in P.SUSTAINED else 0.03) * sr)       # vowel to vowel: a slower glide
            head = audio.cut(xa, x0, xt + ov // 2)
            period = 1.0 / max(60.0, audio.mean_f0(self.load(cx.entry.wav)[1], x_end - 0.1, x_end) or 100.0)
            y_start = int(round(ya * sr)) - ov // 2
            ref = head[-ov:]
            best_d, best_r = 0, -2.0
            for d in range(0, int(period * sr) + 1, 4):
                seg = audio.cut(ya_, y_start + d, y_start + d + ov)
                r = float(np.dot(ref, seg) / (np.linalg.norm(ref) * np.linalg.norm(seg) + 1e-9))
                if r > best_r:
                    best_d, best_r = d, r
            rest = audio.cut(ya_, y_start + best_d, int(round(yz * sr)) + best_d)
            w = np.linspace(0, 1, ov)                 # phase-aligned halves add up: linear, not equal-power
            mid = head[-ov:] * (1 - w) + rest[:ov] * w
            y = np.concatenate([head[:-ov], mid, rest[ov:]])
            ybounds = [b - best_d / sr for b in ybounds]     # Y sits best_d earlier than T
        T = n / sr
        bounds = [T] + [T + (b - ya) for b in ybounds]
        return Clip(cy.entry, [X, Y] + tail, 0.0, len(y) / sr, bounds, [False] * len(bounds),
                    'splice %s+%s' % (X, Y), audio=y)

    def _splice_cv(self, clips, cands, g, C, V):
        """A consonant+vowel the bank never recorded (tsa, kye, si ...): the consonant and the first
        ~30 ms of its vowel from a recorded [C V'], crossfaded into a clean V."""
        if C in P.SUSTAINED or C == 'Sil' or V not in P.VOWELS:
            return None
        h = self._cons_head(clips, cands, g, C)
        s = self._long_segment(clips, g, V)
        if h is None or s is None:
            return None
        cy, cb, ce, v2, ve = h
        cv, vb, vend = s
        sr = TARGET_SR
        xa, ya_ = self.load(cy.entry.wav)[0], self.load(cv.entry.wav)[0]
        keep = min(0.03, 0.5 * (ve - ce))
        ov = int(0.04 * sr)
        head = audio.cut(xa, int(round(cb * sr)), int(round((ce + keep) * sr)) + ov // 2)
        v0 = vb + min(0.08, 0.3 * (vend - vb))
        v1 = min(vend - 0.02, v0 + 0.35)
        if v1 - v0 < 0.05:
            return None
        period = 1.0 / max(60.0, audio.mean_f0(self.load(cv.entry.wav)[1], v0, v0 + 0.1) or 100.0)
        start, ref = int(round(v0 * sr)), head[-ov:]
        best_d, best_r = 0, -2.0
        for d in range(0, int(period * sr) + 1, 4):
            seg = audio.cut(ya_, start + d, start + d + ov)
            r = float(np.dot(ref, seg) / (np.linalg.norm(ref) * np.linalg.norm(seg) + 1e-9))
            if r > best_r:
                best_d, best_r = d, r
        rest = audio.cut(ya_, start + best_d, int(round(v1 * sr)) + best_d)
        # level: the new vowel continues at the level the recorded one started with
        g_db = audio.rms_db(head[-ov:]) - audio.rms_db(rest[:ov])
        rest = rest * 10 ** (float(np.clip(g_db, -9, 9)) / 20)
        w = np.linspace(0, 1, ov)
        y = np.concatenate([head[:-ov], head[-ov:] * (1 - w) + rest[:ov] * w, rest[ov:]]).astype(np.float32)
        return Clip(cy.entry, [C, V], 0.0, len(y) / sr, [ce - cb], [False],
                    'splice %s+%s (from %s %s)' % (C, V, C, v2), audio=y)

    @staticmethod
    def _loud(ft, t0, t1):
        """Does [t0, t1] (absolute seconds) hold anything louder than silence?"""
        f0, f1 = max(0, int(round((t0 - 0.02) / audio.HOP))), min(ft.n, int(round((t1 - 0.02) / audio.HOP)))
        return f1 > f0 and bool((ft.energy[f0:f1] > ft.sil_thr).any())

    # -------------------------------------------------------------- scoring
    def unit_score(self, c, i):
        segs = c.segments()
        s = 2.0 * c.detected[i] + 0.5 * sum(c.detected) - 0.5 * min(c.entry.dup, 2)
        if getattr(self, 'typical', None):
            s -= max(0.0, self._atypical(c, i) - 1.0)        # prefer recordings that sound like the bank
        if c.derived:
            s -= 1.0
        # [C V] is best taken mid-word (VCV), [Sil X] from a real "- X" alias
        if i > 0 and P.pclass(c.phns[i - 1]) == 'vowel':
            s += 0.5
        for p, b, e in (segs[i], segs[i + 1]):
            d = e - b
            if p in P.SUSTAINED and d < 0.06:
                s -= 2
            if p not in P.SUSTAINED and p != 'Sil' and d > 0.3:
                s -= 1
        return s

    def _typical_spectra(self, clips):
        """Per group and sustained phoneme: the bank's typical spectrum and the typical distance to it."""
        acc = defaultdict(list)
        for c in clips:
            if c.derived or c.audio is not None:
                continue
            ft = self.load(c.entry.wav)[1]
            for p, b, e in c.segments():
                if p in P.SUSTAINED and e - b > 0.12:
                    acc[(c.entry.group, p)].append(ft.spec[ft.t2f(b + 0.04):ft.t2f(e - 0.03) + 1].mean(axis=0))
        out = {}
        for k, v in acc.items():
            if len(v) >= 5:
                ref = np.median(v, axis=0)
                out[k] = (ref, float(np.median([np.linalg.norm(s - ref) for s in v])) or 1.0)
        return out

    def _atypical(self, c, i):
        """How unlike the bank's usual sound this unit's vowels are (1 = typical distance)."""
        if c.audio is not None:          # spliced: built from typical parts already
            return 0.0
        ft, worst = self.load(c.entry.wav)[1], 0.0
        for p, b, e in c.segments()[i:i + 2]:
            k = (c.entry.group, p)
            if p in P.SUSTAINED and e - b > 0.12 and k in self.typical:
                ref, md = self.typical[k]
                d = np.linalg.norm(ft.spec[ft.t2f(b + 0.04):ft.t2f(e - 0.03) + 1].mean(axis=0) - ref)
                worst = max(worst, d / md)
        return worst

    def _deep_reattack(self, c, i):
        """VCV singers often re-attack a vowel ("a い" with a little glottal stop). Shallow dips are
        filled when the unit is written; a deep one would hiccup in every legato a-i, so the unit is
        dropped and later spliced from two clean vowels instead."""
        if c.derived or c.audio is not None or not (c.phns[i] in P.SUSTAINED and c.phns[i + 1] in P.SUSTAINED):
            return False
        depth = audio.dip_at(self.load(c.entry.wav)[0], TARGET_SR, c.bounds[i])[0]
        if depth > 12:
            self.skipped.append((c.entry.raw_alias, c.entry.wav, 're-attacked vowel (%.0f dB dip), not used'
                                 % depth))
            return True
        return False

    # -------------------------------------------------------------- loudness
    def _sustained_levels(self, c, x):
        """(phoneme, dB) of the middle of every sustained segment of a clip."""
        out = []
        for p, b, e in c.segments():
            if p in P.SUSTAINED and e - b >= 0.06:
                m0, m1 = b + 0.2 * (e - b), e - 0.2 * (e - b)
                out.append((p, audio.rms_db(audio.cut(x, int(m0 * TARGET_SR), int(m1 * TARGET_SR)))))
        return out

    def level_targets(self, clips):
        """Median level of each sustained phoneme per group, over the recorded clips."""
        lv = defaultdict(list)
        for c in clips:
            if c.derived or c.audio is not None:
                continue
            for p, db in self._sustained_levels(c, self.load(c.entry.wav)[0]):
                lv[(c.entry.group, p)].append(db)
        return {k: float(np.median(v)) for k, v in lv.items()}

    def gain_curve(self, c, x, targets, n):
        """Per-sample gain for a clip: every sustained segment is brought to the bank's usual level on
        its own (a recording can hold a soft first vowel and loud later ones), interpolated across the
        consonants in between."""
        pts = []
        for p, b, e in c.segments():
            if p in P.SUSTAINED and e - b >= 0.06 and (c.entry.group, p) in targets:
                m0, m1 = b + 0.2 * (e - b), e - 0.2 * (e - b)
                db = audio.rms_db(audio.cut(x, int(m0 * TARGET_SR), int(m1 * TARGET_SR)))
                pts.append(((b + e) / 2 - c.start, float(np.clip(targets[(c.entry.group, p)] - db, -9, 9))))
        if not pts:
            return np.ones(n, dtype=np.float32)
        t = np.arange(n) / TARGET_SR
        g = np.interp(t, [q[0] for q in pts], [q[1] for q in pts])
        return (10 ** (g / 20)).astype(np.float32)

    PLOSIVE_LIKE = P.UNV_PLOSIVE | P.V_PLOSIVE | P.UNV_AFFR | {'dz', 'dZ'}

    def _brighten(self, segs, t0, y):
        """Optional treble lift on chosen phonemes (--brighten o=3), faded in/out over 20 ms."""
        if not self.brighten:
            return y
        mask = np.zeros(len(y), dtype=np.float32)
        gain = 0.0
        for p, b, e in segs:
            g = self.brighten.get(p)
            if g:
                gain = g
                i0, i1 = int(round((b - t0) * TARGET_SR)), int(round((e - t0) * TARGET_SR))
                mask[max(0, i0):max(0, i1)] = 1
        if not mask.any():
            return y
        r = int(0.02 * TARGET_SR)
        mask = np.convolve(mask, np.ones(r) / r, 'same')
        return (y * (1 - mask) + audio.high_shelf(y, TARGET_SR, gain) * mask).astype(np.float32)

    BREATHY = {'h', 'C', 'p\\', "p\\'"}
    BREATH_DB = 14.0          # h noise at most this far below the neighbouring vowel

    def _devoice_breath(self, c, y):
        """h / hy / f as pure breath noise: VOCALOID plays unvoiced phonemes as recorded, so harmonics
        in a breathy recorded "h" came out at the recording's pitch, like the raw sample."""
        segs = c.segments()
        for k, (p, b, e) in enumerate(segs):
            if p in self.BREATHY and e - b > 0.02:
                # Many singers voice an h between vowels (Niko's is as loud as the vowel, fully pitched):
                # as noise at that level it hissed. Breath sits well below the vowels around it.
                near = [audio.rms_db(y[int(round((vb + 0.2 * (ve - vb) - c.start) * TARGET_SR)):
                                       int(round((ve - 0.2 * (ve - vb) - c.start) * TARGET_SR))])
                        for q, vb, ve in (segs[j] for j in (k - 1, k + 1) if 0 <= j < len(segs))
                        if q in P.SUSTAINED and ve - vb > 0.05]
                y = audio.devoice(y, TARGET_SR, int(round((b - c.start) * TARGET_SR)),
                                  int(round((e - c.start) * TARGET_SR)),
                                  max_db=max(near) - self.BREATH_DB if near else None)
        return y

    def _silent_closures(self, c, x, y):
        """The closure of an unvoiced plosive/affricate (vowel end -> burst) must be silence. Room noise or
        the vowel's tail there is analysed as unvoiced frames shaped like the vowel, and VOCALOID plays
        them as a hiss before the release: Teto's "t" came out as "f t"."""
        f = int(0.003 * TARGET_SR)
        for k, (p, b, e) in enumerate(c.segments()):
            if p not in P.UNV_PLOSIVE and p not in P.UNV_AFFR:
                continue
            bu = audio.burst_time(x, TARGET_SR, b, e)
            if bu is None or bu - b < 0.015:
                continue
            m0 = int(round((b - c.start) * TARGET_SR)) + f
            m1 = int(round((bu - c.start) * TARGET_SR)) - f
            if m1 - m0 < 2 * f or m0 < f or m1 > len(y):
                continue
            y[m0 - f:m0] *= np.linspace(1, 0, f)
            y[m1:m1 + f] *= np.linspace(0, 1, f)
            y[m0:m1] = audio.cut(x, -(m1 - m0) - 10, -10)       # dither-level silence
        return y

    def _closure_head(self, c, max_len=0.08):
        """A CV entry's oto offset often sits on the release of its plosive, so the clip has no closure:
        VOCALOID then runs the previous vowel straight into the burst ("ke-ko" sang like "ke-ho").
        Pull the start back over the quiet closure the recording has before it."""
        p = c.phns[0]
        if c.audio is None and p in P.NASAL and p != 'N\\':
            return self._nasal_head(c)
        if c.audio is not None or not (P.pclass(p) in ('uplos', 'vplos') or p in P.UNV_AFFR or p in ('dz', 'dZ')):
            return 0.0
        x = self.load(c.entry.wav)[0]
        sr, w = TARGET_SR, int(0.005 * TARGET_SR)

        def lvl(t):
            return audio.rms_db(x[max(0, int(t * sr)):max(0, int(t * sr)) + w])
        v = c.bounds[0]
        quiet = audio.rms_db(x[int((v + 0.03) * sr):int((v + 0.1) * sr)]) - 25
        t = c.start
        if lvl(t) >= quiet:
            for _ in range(5):      # the offset may cut into the burst: step back over its onset first
                if lvl(t - 0.005) < quiet:
                    break
                t -= 0.005
            else:
                return 0.0          # no quiet closure before it (voiced stop, legato recording)
        while t - 0.005 >= max(0.0, c.start - max_len) and lvl(t - 0.005) < quiet:
            t -= 0.005
        if c.start - t < 0.02:
            return 0.0
        d, c.start = c.start - t, t
        return d

    def _nasal_head(self, c, max_len=0.04):
        """The same for a CV nasal: the offset often sits inside the murmur ("ne" kept 35 of its ~55 ms
        of n). Walk back while the sound stays as dark as the murmur (little energy above 1 kHz)."""
        x = self.load(c.entry.wav)[0]
        sr, w = TARGET_SR, int(0.01 * TARGET_SR)
        win = np.hanning(w)
        fr = np.fft.rfftfreq(w, 1 / sr)

        def tilt(t):
            a = int(t * sr)
            if a < 0:
                return None
            S = np.abs(np.fft.rfft(x[a:a + w] * win)) ** 2
            return 10 * np.log10(S[fr > 1000].sum() + 1e-12) - 10 * np.log10(S[fr <= 1000].sum() + 1e-12)
        b, e = c.start, c.bounds[0]
        if e - b > 0.06:
            return 0.0
        ref = max(tilt(b), tilt(b + 0.005) or -99)
        vowel = tilt(e + 0.04)
        if ref is None or vowel is None or vowel - ref < 8:
            return 0.0                # the murmur doesn't stand out from the vowel: can't tell where it starts
        lim = ref + min(3.0, (vowel - ref) / 3)
        t = b
        while t - 0.005 >= b - max_len:
            v = tilt(t - 0.005)
            if v is None or v > lim or audio.rms_db(x[int((t - 0.005) * sr):int(t * sr)]) < -60:
                break
            t -= 0.005
        if b - t < 0.01:
            return 0.0
        c.start = t
        return b - t

    def _mute_release(self, c, units, x, y):
        """In [V C] / [Sil C] with C a plosive or affricate, quiet everything after the cut point: only the
        following [C V] should sound the release (renders showed doubled bursts otherwise). Skipped when
        the same clip also provides that [C V]."""
        for i in set(units):
            C = c.phns[i + 1]
            if C not in self.PLOSIVE_LIKE or (i + 1) in units:
                continue
            art = self.art_seg(c, i, x)
            m = int(round(art.boundaries[2] * TARGET_SR))
            seg_end = c.segments()[i + 1][2] - c.start
            e = min(len(y), int(round(seg_end * TARGET_SR)) + int(0.03 * TARGET_SR))
            f = int(0.004 * TARGET_SR)
            if m + f >= e:
                continue
            floor = 0.1 if C in P.V_PLOSIVE or C in ('dz', 'dZ') else 0.0    # keep a little voice bar
            y[m:m + f] *= np.linspace(1, max(floor, 1e-3), f)
            y[m + f:e] *= floor
            if floor == 0.0:
                y[m + f:e] = audio.cut(x, -(e - m - f) - 10, -10)            # dither, not digital zero
        return y

    # -------------------------------------------------------------- as marks
    @staticmethod
    def _mark_in(seg, t, side, x=None, head=False):
        """Where VOCALOID cuts this unit inside phoneme seg. A consonant is shared by [V C] and [C V]:
        both cut it at the same point, so it plays once and at its natural length."""
        p, b, e = seg
        d = e - b
        if p in P.SUSTAINED:
            m = t - min(0.5 * d, 0.25) if side == 'left' else t + min(0.5 * d, 0.25)
        elif p == 'Sil':
            m = t - min(0.6 * d, 0.08) if side == 'left' else t + min(0.6 * d, 0.08)
        else:
            m = b + 0.5 * d
            if P.pclass(p) in ('uplos', 'vplos') or p in P.UNV_AFFR or p in ('dz', 'dZ'):
                # plosives and affricates: cut in the closure, before the burst, or the burst plays twice
                bu = audio.burst_time(x, TARGET_SR, b, e) if x is not None else None
                # same distance before the burst in [V C] and [C V], so if VOCALOID crossfades the two
                # units across the cut, their bursts coincide instead of sounding as a double release
                # (a burst right at the segment start still counts: cutting after it plays it twice)
                m = max(b + 0.002, bu - 0.015) if bu is not None else b + 0.3 * d
                if head and side == 'left' and bu is not None:
                    # a CV clip that starts with its own (silent) closure: nothing before it shares the
                    # cut, and 15 ms of closure made the stop sound rushed (Niko's "ke-ko")
                    m = max(b + 0.003, bu - 0.04)
        return min(max(m, b + 0.002), e - 0.002)

    def _vowel_mark(self, c, seg, m, side):
        """Move a mark inside a vowel to where it sounds most like the bank's usual vowel. VOCALOID
        crossfades from the unit into the held vowel (and out of it into the next unit) at this mark;
        a recording whose vowel drifts (Niko's "ne" turns odd 0.1 s in) made that join stand out."""
        p, b, e = seg
        key = (c.entry.group, p)
        if p not in P.VOWELS or key not in self.typical or c.audio is not None or c.derived:
            return m
        ref, md = self.typical[key]
        lo, hi = (b + 0.05, m) if side == 'right' else (m, e - 0.05)
        if hi - lo < 0.03:
            return m
        ft = self.load(c.entry.wav)[1]
        f0, f1 = ft.t2f(lo), ft.t2f(hi)
        sp = ft.spec[max(0, f0 - 1):f1 + 2]
        sm = (sp[:-2] + sp[1:-1] + sp[2:]) / 3 if len(sp) >= 3 else sp
        best, bt = None, m
        for k in range(min(len(sm), f1 - f0 + 1)):
            t = ft.f2t(f0 + k)
            score = np.linalg.norm(sm[k] - ref) / md + 2.0 * abs(t - m)    # stay near the usual place
            if best is None or score < best:
                best, bt = score, t
        return bt

    def art_seg(self, c, i, x=None):
        segs = c.segments()
        t = c.bounds[i]
        b = [self._vowel_mark(c, segs[i], self._mark_in(segs[i], t, 'left', x, head=i == 0), 'left') - c.start,
             t - c.start,
             self._vowel_mark(c, segs[i + 1], self._mark_in(segs[i + 1], t, 'right', x), 'right') - c.start]
        n = int(round((c.end - c.start) * TARGET_SR))
        return dbtool_io.ArtSeg([c.phns[i], c.phns[i + 1]], 0, n, b,
                                [self.voiced(c.phns[i]), self.voiced(c.phns[i + 1])])

    # -------------------------------------------------------------- main
    def _rank_stationary(self, clips, joins, g, p, lst):
        """Order stationary candidates [(len, clip, begin, end)]: the held sound should match the units
        it is joined to (VOCALOID crossfades from each [C V] into it at the unit's mark, and out of it
        into each [V C]), be long, and have no seam."""
        lst = sorted(lst, key=lambda r: -r[0])
        ref, md = joins.get((g, p)) or self.typical.get((g, p), (None, 1.0))

        def typ(ci, sb, se):
            if ref is None:
                return 0.0
            ft = self.load(clips[ci].entry.wav)[1]
            return float(np.linalg.norm(ft.spec[ft.t2f(sb):ft.t2f(se) + 1].mean(axis=0) - ref)) / md
        # A short stationary is looped many times on a held note and the repeat is audible, so length
        # counts most: among the candidates that sound usual enough, take the longest (up to ~0.8 s).
        ranked = sorted(lst, key=lambda r: typ(r[1], r[2], r[3]) - 2 * min(r[0], 1.0))
        clean = []
        for _, ci, sb, se in ranked[:16]:
            cb, ce = audio.clean_span(self.load(clips[ci].entry.wav)[0], TARGET_SR, sb, se)
            if ce - cb >= 0.15:
                clean.append((typ(ci, cb, ce) + 0.05 * clips[ci].entry.dup, ci, cb, ce))
        if not clean:
            return lst
        lim = max(1.3, min(r[0] for r in clean) + 0.5)
        ok = [r for r in clean if r[0] <= lim]
        ok.sort(key=lambda r: r[0] - 3 * min(r[3] - r[2], 0.8))      # joining takes reaches ~0.8 s anyway
        return [(-k, ci, cb, ce) for k, ci, cb, ce in ok]

    def _join_spectra(self, clips, chosen):
        """Per group and sustained phoneme: the median spectrum where the chosen units hand over to a
        held note (their marks inside that phoneme), and the median distance to it."""
        acc = defaultdict(list)
        for ci, units in chosen.items():
            c = clips[ci]
            if c.derived or c.audio is not None:
                continue
            ft = self.load(c.entry.wav)[1]
            segs = c.segments()
            for i in set(units):
                for k, side in ((i, 'left'), (i + 1, 'right')):
                    if segs[k][0] in P.SUSTAINED or segs[k][0] in P.NASAL:
                        m = self._mark_in(segs[k], c.bounds[i], side)
                        acc[(c.entry.group, segs[k][0])].append(ft.spec[ft.t2f(m)])
        out = {}
        for k, v in acc.items():
            if len(v) >= 5:
                ref = np.median(v, axis=0)
                out[k] = (ref, float(np.median([np.linalg.norm(s - ref) for s in v])) or 1.0)
        return out

    @staticmethod
    def _match_timbre(y, ref, max_db=6.0, lo_hz=300.0):
        """EQ a stationary's spectral envelope toward the spectrum at the unit marks it is joined to.
        A long held recording ("V V V V") can be voiced differently from the same vowel inside words
        (Teto's V: one formant region ~2.8 log units off), which made every held note shift colour."""
        ft = audio.Features(y, TARGET_SR)
        voiced = ft.spec[ft.voiced] if ft.voiced.any() else ft.spec
        d = (ref - voiced.mean(axis=0)) * (20 / np.log(10))          # natural-log magnitude -> dB
        hz = audio.band_centres_hz(TARGET_SR)
        d = d - np.median(d[hz >= lo_hz])                                # level is matched separately
        d = np.convolve(np.pad(d, 1, mode='edge'), np.ones(3) / 3, 'valid')
        d = np.clip(d, -max_db, max_db) * (hz >= lo_hz)
        return audio.band_eq(y, TARGET_SR, hz, 0.7 * d)                  # most of the way, not all

    def _stat_body(self, clips, takes, want=0.8, ov=0.08):
        """The steady vowel of a stationary, from takes [(score, clip, begin, end)], best first. VOCALOID
        loops it on held notes and a short loop is heard repeating, so a short take is lengthened with
        further takes of the same vowel at the same pitch: level-matched, phase-aligned, crossfaded.
        Returns (audio, level dB, number of takes)."""
        sr, n = TARGET_SR, int(ov * TARGET_SR)
        _, ci, sb, se = takes[0]
        x, ft = self.load(clips[ci].entry.wav)
        y = audio.cut(x, int(round(sb * sr)), int(round(se * sr)))
        lvl = audio.rms_db(y[len(y) // 5:len(y) - len(y) // 5])
        f0 = audio.mean_f0(ft, sb, se) or 100.0
        period = int(sr / max(60.0, f0))
        used = {ci}
        for _, cj, tb, te in takes[1:]:
            if len(y) >= want * sr:
                break
            if cj in used or te - tb < 0.15:
                continue
            xj, fj = self.load(clips[cj].entry.wav)
            fz = audio.mean_f0(fj, tb, te)
            if not fz or abs(1200 * np.log2(fz / f0)) > 30:
                continue
            z = audio.cut(xj, int(round(tb * sr)), int(round(te * sr)))
            z = z * 10 ** ((lvl - audio.rms_db(z[len(z) // 5:len(z) - len(z) // 5])) / 20)
            ref = y[-n:]
            best_d, best_r = 0, -2.0
            for d in range(0, period + 1, 2):
                seg = z[d:d + n]
                r = float(np.dot(ref, seg) / (np.linalg.norm(ref) * np.linalg.norm(seg) + 1e-9))
                if r > best_r:
                    best_d, best_r = d, r
            if best_r < 0.5:            # the waveforms don't line up: the join would flutter
                continue
            z = z[best_d:]
            w = np.linspace(0, 1, n)    # phase-aligned halves add up: linear, not equal-power
            y = np.concatenate([y[:-n], y[-n:] * (1 - w) + z[:n] * w, z[n:]])
            used.add(cj)
        return y, lvl, len(used)

    def run(self):
        os.makedirs(self.out, exist_ok=True)
        for fn in os.listdir(self.out):
            if re.match(r'.*\.(wav|trans|seg|as\d+)$', fn):
                os.remove(os.path.join(self.out, fn))
        clips = []
        self.n_closure = 0
        for k, e in enumerate(self.entries):
            if k % 200 == 0:
                self.log('  analysing %d/%d' % (k, len(self.entries)))
            phs, why = self.map_phonemes(e)
            if phs is None:
                self.skipped.append((e.raw_alias, e.wav, why))
                continue
            if not os.path.exists(e.wav):
                self.skipped.append((e.raw_alias, e.wav, 'wav missing'))
                continue
            c, why = self.segment(e, phs)
            if c is None:
                self.skipped.append((e.raw_alias, e.wav, why))
                continue
            if self._closure_head(c):
                self.n_closure += 1
            clips.append(c)
            if self.derive:
                clips.extend(self.derive_silence(c))

        # pick the best clip for every (group, diphone)
        self.typical = self._typical_spectra(clips)
        n_src = defaultdict(set)                  # recordings per unit: an odd one is only dropped if
        for c in clips:                           # another recording or a splice can replace it
            for i in range(len(c.phns) - 1):
                n_src[(c.entry.group, c.phns[i], c.phns[i + 1])].add(id(c.entry))
        cands = defaultdict(list)
        for ci, c in enumerate(clips):
            for i in range(len(c.phns) - 1):
                if self._deep_reattack(c, i):
                    continue
                replaceable = (len(n_src[(c.entry.group, c.phns[i], c.phns[i + 1])]) > 1
                               or c.phns[i] in P.SUSTAINED or c.phns[i] in P.NASAL)
                natural = any(p in P.EXTRAS for p in c.phns)     # a vowel trailing into a breath is meant to
                if self.splice and replaceable and not natural and self._atypical(c, i) > 2.0:
                    self.skipped.append((c.entry.raw_alias, c.entry.wav,
                                         'vowel sounds unlike the rest of the bank (e.g. a trailing-off '
                                         'last syllable), not used for [%s %s]' % (c.phns[i], c.phns[i + 1])))
                    continue
                if self.splice and c.audio is None and c.phns[i] == c.phns[i + 1] and c.phns[i] in P.VOWELS:
                    continue        # legato o-o: a smooth splice beats a re-attacked "o お" recording
                if (c.phns[i], c.phns[i + 1]) in self.avoid and c.audio is None:
                    continue        # --avoid: rebuilt from other recordings instead
                cands[(c.entry.group, c.phns[i], c.phns[i + 1])].append((self.unit_score(c, i), ci, i))
        chosen = defaultdict(list)
        for key, lst in cands.items():
            lst.sort(key=lambda r: -r[0])
            for _, ci, i in lst[:self.keep]:
                chosen[ci].append(i)
        if self.fill:
            self.fill_missing(clips, cands, chosen)

        # stationaries: the longest steady stretch of each sustained phoneme. Follow it through the
        # recording (oto regions stop early); VOCALOID loops this on long notes, so longer is smoother.
        stat = defaultdict(list)
        for ci, c in enumerate(clips):
            if c.derived or c.audio is not None:
                continue
            ft = self.load(c.entry.wav)[1]
            for p, b, e in c.segments():
                if p in P.SUSTAINED and e - b >= 0.06:
                    # also search from later in the segment: a diphthong (aI, @U) glides first and only
                    # holds its end, and a CV vowel can still be settling right after the consonant
                    starts = [b + 0.04] + [t for t in (b + 0.5 * (e - b), e - 0.06) if t > b + 0.1]
                    sb, se = max((audio.sustained_extent(ft, t) for t in starts), key=lambda r: r[1] - r[0])
                    if se - sb >= 0.15:
                        sb, se = audio.flat_span(self.load(c.entry.wav)[0], TARGET_SR, sb, se)
                    if se - sb >= 0.15:
                        stat[(c.entry.group, p)].append((se - sb - 0.05 * c.entry.dup, ci, sb, se))
        targets = self.level_targets(clips) if self.normalize else {}

        groups = sorted({c.entry.group for c in clips})
        gid = {g: 'g%02d' % i for i, g in enumerate(groups)}
        manifest, nfile = [], 0
        produced = defaultdict(set)

        for ci in sorted(chosen):
            c = clips[ci]
            if c.audio is not None:
                x, ft = c.audio, audio.Features(c.audio, TARGET_SR)
            else:
                x, ft = self.load(c.entry.wav)
            sx = self.suffixer(c.entry.group)
            nfile += 1
            name = '%s_%05d' % (gid[c.entry.group], nfile)
            base = os.path.join(self.out, name)
            a, b = int(round(c.start * TARGET_SR)), int(round(c.end * TARGET_SR))
            y = audio.cut(x, a, b)
            # a Sil segment must be silent: mute what the source has there
            mb, ma = c.mute_before, c.mute_after
            if mb is None and c.phns[0] == 'Sil' and (self._loud(ft, c.start, c.bounds[0])
                                                      or c.phns[1] in P.SUSTAINED):
                # a vowel's [Sil V] gets truly clean silence: room noise or a lip click before the attack
                # makes VOCALOID fade the vowel in slowly and breathily (measured: 200 ms vs 15 ms)
                mb = c.bounds[0]
            if ma is None and c.phns[-1] == 'Sil' and self._loud(ft, c.bounds[-1], c.end):
                ma = c.bounds[-1]
            if c.phns[0] == 'Sil' and c.phns[1] in P.SUSTAINED:
                # a vowel attacking from silence: round off hard (glottal) onsets, which the DBTool reads
                # as noise and VOCALOID then fades in slowly and breathily
                o = int(round((c.bounds[0] - c.start) * TARGET_SR))
                n = min(len(y) - o, int(0.025 * TARGET_SR))
                if o >= 0 and n > 0:
                    y[o:o + n] *= 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))
            if mb is not None:
                m = int(round((mb - c.start) * TARGET_SR))
                y[:m] = audio.cut(x, -m - 10, -10)          # dither
                fin = 0.004 if P.pclass(c.phns[1]) in ('uplos', 'ufric') else 0.015
                fade = min(len(y) - m, int(fin * TARGET_SR))
                y[m:m + fade] *= np.linspace(0, 1, fade)
            if ma is not None:
                m = int(round((ma - c.start) * TARGET_SR))
                fade = min(m, int(0.04 * TARGET_SR))
                y[m - fade:m] *= np.linspace(1, 0, fade)
                y[m:] = audio.cut(x, -(len(y) - m) - 10, -10)
            for i in set(chosen[ci]):
                if c.phns[i] in P.SUSTAINED and c.phns[i + 1] in P.SUSTAINED:
                    y, _ = audio.fill_dip(y, TARGET_SR, c.bounds[i] - c.start)
            y = self._mute_release(c, chosen[ci], x, y)
            y = self._silent_closures(c, x, y)
            y = self._devoice_breath(c, y)
            y = self._brighten(c.segments(), c.start, y)
            if targets:
                y = y * self.gain_curve(c, x, targets, len(y))
            audio.write_wav(base + '.wav', y, TARGET_SR)
            c.end = c.start + (b - a) / TARGET_SR
            segs = [(sx(p), s - c.start, e - c.start) for p, s, e in c.segments()]
            segs[-1] = (segs[-1][0], segs[-1][1], (b - a) / TARGET_SR)
            dbtool_io.write_seg(base + '.seg', segs)
            units = sorted(set(chosen[ci]))
            dirs = []
            for k, i in enumerate(units):
                unit = [c.phns[i], c.phns[i + 1]]
                occ = sum(1 for j in range(i + 1) if c.phns[j:j + 2] == unit)
                dirs.append(([sx(p) for p in unit], occ))
                art = self.art_seg(c, i, x)
                art.phns = [sx(p) for p in art.phns]
                dbtool_io.write_as('%s.as%d' % (base, k), art)
                produced[c.entry.group].add(tuple(unit))
                f0 = audio.mean_f0(ft, c.bounds[i] - 0.1, c.bounds[i] + 0.1)
                manifest.append([name, 'art', ' '.join(sx(p) for p in unit), c.entry.group, c.entry.raw_alias,
                                 os.path.relpath(c.entry.wav, self.bank), '%.1f' % f0,
                                 'yes' if c.detected[i] else 'oto', c.derived])
            dbtool_io.write_trans(base + '.trans', [sx(p) for p in c.phns], dirs)

        joins = self._join_spectra(clips, chosen)
        final = {}
        for (g, p), lst in sorted(stat.items()):
            final[(g, p)] = self._rank_stationary(clips, joins, g, p, lst)
        jobs = [(g, p, p, lst) for (g, p), lst in sorted(final.items())]
        # VOCALOID turns ん into m / n / N / N' / J before the next consonant and holds that phoneme on a
        # long note, so each needs a stationary too. Banks only hold the plain ん: build them from its
        # takes, EQ'd toward how that nasal sounds in the bank's own units.
        for g in sorted({g for g, _ in final}):
            if (g, 'N\\') in final and self.lang == 'ja':
                for n in sorted(P.NASAL - {'N\\'}):
                    if (g, n) not in final and (self.dict is None or n in self.dict):
                        jobs.append((g, n, 'N\\', final[(g, 'N\\')]))
        nstat = 0
        for g, p, src, lst in jobs:
            for k, (_, ci, sb, se) in enumerate(lst[:self.keep]):
                c = clips[ci]
                x, ft = self.load(c.entry.wav)
                sx = self.suffixer(g)
                nfile += 1
                nstat += 1
                name = '%s_%05d' % (gid[g], nfile)
                base = os.path.join(self.out, name)
                body, lvl, ntake = self._stat_body(clips, [lst[k]] + lst[self.keep:])
                if (g, p) in joins:
                    body = self._match_timbre(body, joins[(g, p)][0])
                # The DBTool analyses the whole phoneme segment of a stationary (it ignores the
                # .as marks) and indexes its neighbours, so a lone "a" crashes it. Write the steady
                # part only, faded and padded with silence: "Sil a Sil" + [a].
                pad = int(0.2 * TARGET_SR)
                fade = int(0.01 * TARGET_SR)
                y = np.concatenate([audio.cut(x, -pad - 10, -10), body, audio.cut(x, -pad - 10, -10)])
                y[pad:pad + fade] *= np.linspace(0, 1, fade)
                y[-pad - fade:-pad] *= np.linspace(1, 0, fade)
                if self.brighten.get(p):
                    y = audio.high_shelf(y, TARGET_SR, self.brighten[p])
                tgt = targets.get((g, p), targets.get((g, src)))
                if tgt is not None:
                    y = y * 10 ** (float(np.clip(tgt - lvl, -9, 9)) / 20)
                audio.write_wav(base + '.wav', y, TARGET_SR)
                p0, p1, d = pad / TARGET_SR, (pad + len(body)) / TARGET_SR, len(y) / TARGET_SR
                # The DBTool analyses the whole stationary segment, so its edges must already be at full
                # level: the fades sit in the Sil segments, and the vowel segment starts 50 ms after them
                # (past the analysis window). Otherwise VOCALOID plays the faded edge as a new attack.
                edge = fade / TARGET_SR + min(0.05, 0.15 * (p1 - p0))
                p0, p1 = p0 + edge, p1 - edge
                dbtool_io.write_seg(base + '.seg', [('Sil', 0.0, p0), (sx(p), p0, p1), ('Sil', p1, d)],
                                    stationaries=True)
                dbtool_io.write_trans(base + '.trans', ['Sil', sx(p), 'Sil'], [([sx(p)], 1)])
                inset = min(0.02, 0.1 * (p1 - p0))
                dbtool_io.write_as(base + '.as0', dbtool_io.ArtSeg([sx(p)], 0, len(y), [p0 + inset, p1 - inset],
                                                                   [self.voiced(p)]))
                how = [] if ntake == 1 else ['joined %d takes' % ntake]
                if src != p:
                    how.append('from %s' % src)
                manifest.append([name, 'stationary', sx(p), g, c.entry.raw_alias,
                                 os.path.relpath(c.entry.wav, self.bank), '%.1f' % audio.mean_f0(ft, sb, se),
                                 '', ', '.join(how)])

        with open(os.path.join(self.out, 'manifest.csv'), 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(['file', 'kind', 'unit', 'group', 'utau_alias', 'utau_wav', 'f0_hz',
                        'boundary_detected', 'derived'])
            w.writerows(manifest)
        self.dict_out = None
        if self.dict is not None:
            sfx = sorted({v for v in self.color_suffix.values() if v})
            dbtool_io.write_dictionary(os.path.join(self.out, 'dictionary.txt'), self.dict, sfx)
            self.dict_out = dbtool_io.load_dictionary(os.path.join(self.out, 'dictionary.txt'))
        self.write_report(produced, groups, gid, len(manifest) - nstat, nstat, manifest)
        return manifest

    # -------------------------------------------------------------- report
    def expected_units(self):
        if self.lang == 'en':
            return self._expected_en()
        if self.lang != 'ja':
            return set()
        V = ['a', 'i', 'M', 'e', 'o']
        cons, cv = set(), set()
        for rom in set(P.KANA.values()):
            ph = P.ja_syllable(rom)
            if ph and len(ph) == 2:
                cv.add(tuple(ph))
                cons.add(ph[0])
        for c in list(cons):
            if c in P.JA_INITIAL:
                cons.add(P.JA_INITIAL[c])
                for v in V:
                    if (c, v) in cv:
                        cv.add((P.JA_INITIAL[c], v))
        exp = set(cv)
        for c in cons:
            if c in P.JA_INITIAL:                       # z/Z: medial only, dz/dZ after Sil and ん
                exp |= {(v, c) for v in V}
            elif c in P.JA_INITIAL.values():
                exp |= {('Sil', c), (P.nasal_before(c), c)}
            else:
                exp |= {(v, c) for v in V} | {(P.nasal_before(c), c), ('Sil', c)}
        exp |= {('Sil', v) for v in V} | {(v, 'Sil') for v in V} | {(a, b) for a in V for b in V}
        exp |= {(v, 'N\\') for v in V} | {('N\\', 'Sil'), ('Sil', 'N\\')} | {(P.nasal_before(v), v) for v in V}
        # ん before a consonant becomes m / n / N / N': every vowel needs a way into each ("a-n-ka" = [a N] [N k])
        exp |= {(v, P.nasal_before(c)) for v in V for c in cons}
        if self.dict is not None:
            exp = {u for u in exp if all(p in self.dict for p in u)}
        return exp

    def _expected_en(self):
        """English: every [C V], [V C], [V V] and the edges to silence, over the phonemes the bank uses,
        plus the consonant clusters it recorded (st, sk, pl ...)."""
        seen, clusters = set(), set()
        for e in self.entries:
            ph = P.en_alias(e.alias)
            if not ph:
                continue
            seen.update(p for p in ph if p != 'Sil')
            for a, b in zip(ph, ph[1:]):
                if a not in P.SUSTAINED and b not in P.SUSTAINED and 'Sil' not in (a, b):
                    clusters.add((a, b))
        if self.dict is not None:
            seen = {p for p in seen if p in self.dict}
        V = {p for p in seen if p in P.VOWELS}
        C = seen - V
        exp = {('Sil', v) for v in V} | {(v, 'Sil') for v in V} | {('Sil', c) for c in C} | {(c, 'Sil') for c in C}
        exp |= {(c, v) for c in C for v in V} | {(v, c) for v in V for c in C} | {(a, b) for a in V for b in V}
        return exp | clusters

    def write_report(self, produced, groups, gid, n_art, n_stat, manifest=()):
        exp = self.expected_units()
        made = defaultdict(lambda: defaultdict(int))
        for row in manifest:
            if row[1] == 'art':
                made[row[3]][row[8].split(' ')[0] if row[8] else 'recorded'] += 1
        lines = ['UTAU -> VOCALOID3 DBTool conversion report', '',
                 'bank: %s' % self.bank, 'language: %s' % self.lang,
                 'articulation units: %d, stationaries: %d' % (n_art, n_stat)]
        if getattr(self, 'n_closure', 0):
            lines.append('CV entries whose offset cut into the consonant (closure, nasal murmur), start moved back: %d'
                         % self.n_closure)
        if getattr(self, 'extras_found', None):
            units = sorted({row[2] for row in manifest if row[1] == 'art'
                            and any(p.split('#')[0] in P.EXTRAS for p in row[2].split())})
            lines.append('extras (type these phonemes in the editor): %s' % ', '.join(self.extras_found))
            what = {'brE': 'breath out at the end of a phrase', 'brI': 'breath in', 'rr': 'rolled r'}
            for x in self.extras_found:
                src = sorted({row[4] for row in manifest if row[1] == 'art' and not row[8]
                              and x in [p.split('#')[0] for p in row[2].split()]})
                lines.append('  %-4s %-36s from: %s' % (x, what.get(x, 'breath'), ', '.join(src) or '-'))
            lines.append('  units: %s' % ', '.join('[%s]' % u for u in units))
        lines.append('')
        if self.dict is not None:
            lines += ['Create the singer database from dictionary.txt in this folder',
                      '(DBTool: File > New singer database...), then run the build steps.', '']
        if any(self.color_suffix.values()):
            lines.append('voice colours (EVEC-style phoneme suffixes):')
            for k, v in self.color_suffix.items():
                lines.append('  %-4s %s' % (v or '(none)', k))
            lines.append('')
        for g in groups:
            have = produced.get(g, set())
            how = ', '.join('%s %d' % kv for kv in sorted(made[g].items(), key=lambda kv: -kv[1]))
            lines.append('[%s] %s (suffix %s): %d diphones (%s)' % (
                gid[g], g, self.color_suffix[self.group_color[g]] or 'none', len(have), how))
            if exp:
                miss = sorted(exp - have)
                lines.append('  coverage %d/%d of the standard %s set' % (len(exp & have), len(exp),
                                                                         'English' if self.lang == 'en' else 'Japanese'))
                if miss:
                    lines.append('  missing: ' + ' '.join('[%s]' % ' '.join(u) for u in miss))
            lines.append('')
        if self.skipped:
            lines.append('skipped oto entries (%d):' % len(self.skipped))
            reasons = defaultdict(list)
            for alias, wav, why in self.skipped:
                reasons[why].append(alias)
            for why, al in sorted(reasons.items(), key=lambda r: -len(r[1])):
                lines.append('  %s (%d): %s' % (why, len(al), ' | '.join(al[:40]) + (' ...' if len(al) > 40 else '')))
        with open(os.path.join(self.out, 'report.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        for ln in lines:
            if ln.startswith('skipped'):
                break
            self.log(ln)
