"""UTAU alias -> VOCALOID phoneme sequence (Japanese kana/romaji VCV/CVVC, English Arpasing)."""
import re

# ------------------------------------------------------------------ phoneme classes

SIL = {'Sil', '?', 'Asp'}
VOWELS = {'a', 'i', 'M', 'e', 'o', 'u',
          'I', 'e', '{', 'Q', 'V', 'U', '@', 'i:', 'u:', 'O:', '@r', 'eI', 'aI', 'OI', '@U', 'aU',
          'I@', 'e@', 'U@', 'O@', 'Q@', '@l', 'e@0', 'A', 'aj', 'aw', 'ej'}
UNV_PLOSIVE = {'p', "p'", 't', "t'", 'k', "k'", 'ph', 'th', 'kh'}
UNV_FRIC = {'s', 'S', 'C', 'p\\', "p\\'", 'h', 'f', 'T', 'x', 'X'}
UNV_AFFR = {'ts', 'tS'}
V_PLOSIVE = {'b', "b'", 'd', "d'", 'g', "g'", 'bh', 'dh', 'gh'}
V_FRIC = {'z', 'Z', 'dz', 'dZ', 'v', 'D', 'h\\'}
NASAL = {'n', 'N', "N'", 'J', 'N\\', 'm', "m'"}
LIQUID = {'4', "4'", 'l', 'l0', 'r', 'R', 'rr'}
SEMI = {'w', 'j'}
SUSTAINED = VOWELS | {'N\\'}          # get stationaries

# extras: breaths (br1..br5 standalone, brE breath out after a vowel, brI breath in) and a rolled r
BREATHS = {'br1', 'br2', 'br3', 'br4', 'br5', 'brE', 'brI'}
TRILL = {'rr'}
EXTRAS = BREATHS | TRILL
UNV_FRIC_EXTRA = BREATHS                 # boundary finding treats a breath like an unvoiced fricative
EXTRA_DICT = {**{b: ('Breaths', False) for b in BREATHS}, 'rr': ('Trills', True)}
EXTRA_FOLDER = re.compile(r'extra|エクストラ|おまけ|息|吸|breath|ブレス', re.I)

TYPICAL_DUR = {}
for _s, _d in ((UNV_PLOSIVE, .07), (UNV_FRIC, .10), (UNV_AFFR, .10), (V_PLOSIVE, .05), (V_FRIC, .07),
               (NASAL, .06), (LIQUID, .03), (SEMI, .05)):
    for _p in _s:
        TYPICAL_DUR[_p] = _d
TYPICAL_DUR['h'] = .08
TYPICAL_DUR['N\\'] = .12


def pclass(ph):
    if ph in SIL:
        return 'sil'
    if ph in BREATHS:
        return 'ufric'
    if ph in VOWELS:
        return 'vowel'
    if ph in UNV_PLOSIVE:
        return 'uplos'
    if ph in UNV_FRIC or ph in UNV_AFFR:
        return 'ufric'
    if ph in V_PLOSIVE:
        return 'vplos'
    if ph in NASAL or ph in LIQUID or ph in SEMI or ph in V_FRIC:
        return 'vcons'
    return 'vcons'


def is_unvoiced(ph):
    return pclass(ph) in ('sil', 'uplos', 'ufric')


# ------------------------------------------------------------------ Japanese

JA_VOWEL = {'a': 'a', 'i': 'i', 'u': 'M', 'e': 'e', 'o': 'o'}
# consonant label -> (plain, palatal)
JA_CONS = {
    '': (None, None), 'k': ('k', "k'"), 'g': ('g', "g'"), 's': ('s', 's'), 'sh': ('S', 'S'),
    'z': ('z', 'z'), 'j': ('Z', 'Z'), 't': ('t', "t'"), 'ch': ('tS', 'tS'), 'ts': ('ts', 'ts'),
    'd': ('d', "d'"), 'n': ('n', 'J'), 'h': ('h', 'C'), 'f': ('p\\', "p\\'"), 'b': ('b', "b'"),
    'p': ('p', "p'"), 'm': ('m', "m'"), 'y': ('j', 'j'), 'r': ('4', "4'"), 'w': ('w', 'w'),
    'v': ('b', "b'"),
}
# y-glide labels -> base label
JA_PAL = {'ky': 'k', 'gy': 'g', 'ty': 't', 'dy': 'd', 'ny': 'n', 'hy': 'h', 'fy': 'f', 'by': 'b',
          'py': 'p', 'my': 'm', 'ry': 'r', 'vy': 'v'}
# initial (after silence / N) variants of medial fricatives
JA_INITIAL = {'z': 'dz', 'Z': 'dZ'}

# relabel substitutes used to fill units a bank never recorded (closures sound alike)
SUBST = {"k'": ['k'], "g'": ['g'], "t'": ['t'], "d'": ['d'], "p'": ['p'], "b'": ['b'], "m'": ['m'],
         "4'": ['4'], 'J': ['n'], 'C': ['h'], "p\\'": ['p\\'], 'dz': ['z'], 'dZ': ['Z'], 'z': ['dz'],
         'Z': ['dZ'], 'N': ['N\\', 'n'], "N'": ['N\\', 'n'], 'm': ['N\\'], 'n': ['N\\']}
# in [C V] units only these are close enough to swap
SUBST_CV = {"p\\'": ['p\\'], 'dz': ['z'], 'dZ': ['Z'], 'z': ['dz'], 'Z': ['dZ']}

# phonemes the devkit's Japanese dictionary lacks: added to the generated dictionary.txt
JA_EXTRA = {'b': ('VoicedPlosives', True), "b'": ('VoicedPlosives', True),
            "p\\'": ('UnvoicedFricatives', False)}

# words in UTAU folder names that name the recording method, not a voice colour
METHOD_WORDS = re.compile(r'連続音|単独音|音源|CVVC|VCV|CV', re.I)
NUMBERING = re.compile(r'(^|/)\s*\d+\s*[.)_\-]\s*')     # "4. F3", "11. CONSONANT RELEASES"
MAIN_COLOR = re.compile(r'normal|standard|std|main|default|通常|ノーマル|標準', re.I)

# fallbacks when the DB dictionary lacks a phoneme
JA_FALLBACK = {"p\\'": 'p\\', 'h\\': 'h', "b'": 'b'}

_KANA = """
あ a い i う u え e お o か ka き ki く ku け ke こ ko が ga ぎ gi ぐ gu げ ge ご go
さ sa し shi す su せ se そ so ざ za じ ji ず zu ぜ ze ぞ zo た ta ち chi つ tsu て te と to
だ da ぢ ji づ zu で de ど do な na に ni ぬ nu ね ne の no は ha ひ hi ふ fu へ he ほ ho
ば ba び bi ぶ bu べ be ぼ bo ぱ pa ぴ pi ぷ pu ぺ pe ぽ po ま ma み mi む mu め me も mo
や ya ゆ yu よ yo ら ra り ri る ru れ re ろ ro わ wa ゐ wi ゑ we を o ん n ゔ vu
ぁ a ぃ i ぅ u ぇ e ぉ o
きゃ kya きゅ kyu きぇ kye きょ kyo ぎゃ gya ぎゅ gyu ぎぇ gye ぎょ gyo
しゃ sha しゅ shu しぇ she しょ sho じゃ ja じゅ ju じぇ je じょ jo すぃ si ずぃ zi
ちゃ cha ちゅ chu ちぇ che ちょ cho てぃ ti てゅ tyu とぅ tu でぃ di でゅ dyu どぅ du
つぁ tsa つぃ tsi つぇ tse つぉ tso にゃ nya にゅ nyu にぇ nye にょ nyo
ひゃ hya ひゅ hyu ひぇ hye ひょ hyo ふぁ fa ふぃ fi ふぇ fe ふぉ fo ふゅ fyu
びゃ bya びゅ byu びぇ bye びょ byo ぴゃ pya ぴゅ pyu ぴぇ pye ぴょ pyo
みゃ mya みゅ myu みぇ mye みょ myo りゃ rya りゅ ryu りぇ rye りょ ryo
いぇ ye うぃ wi うぇ we うぉ wo ゔぁ va ゔぃ vi ゔぇ ve ゔぉ vo
"""
KANA = dict(zip(_KANA.split()[0::2], _KANA.split()[1::2]))
JA_CONS_LABELS = sorted(set(JA_CONS) | set(JA_PAL), key=len, reverse=True)


def kata_to_hira(s):
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヴ' else c for c in s)


def kana_to_romaji(s):
    s = kata_to_hira(s)
    out, i = [], 0
    while i < len(s):
        if s[i:i + 2] in KANA:
            out.append(KANA[s[i:i + 2]])
            i += 2
        elif s[i] in KANA:
            out.append(KANA[s[i]])
            i += 1
        else:
            return None
    return out


def ja_syllable(rom):
    """romaji syllable -> list of phonemes (without context rules), or None."""
    rom = rom.lower()
    if rom in ('n', 'nn', "n'"):
        return ['N\\']
    if rom == 'shi':
        return ['S', 'i']
    if rom == 'chi':
        return ['tS', 'i']
    if rom == 'tsu':
        return ['ts', 'M']
    if rom == 'fu':
        return ['p\\', 'M']
    if rom == 'ji':
        return ['Z', 'i']
    if not rom or rom[-1] not in JA_VOWEL:
        return None
    label, v = rom[:-1], rom[-1]
    vowel = JA_VOWEL[v]
    if label in JA_PAL:
        cons = JA_CONS[JA_PAL[label]][1]
    elif label in JA_CONS:
        plain, pal = JA_CONS[label]
        cons = pal if v == 'i' and label not in ('s', 'z', 'ts', 'w', 'y') else plain
    else:
        return None
    return [vowel] if cons is None else [cons, vowel]


def ja_consonant(label):
    """CVVC VC right side ('k', 'ky', 'sh', 'n' ...) -> consonant phoneme or None."""
    label = label.lower()
    if label in JA_PAL:
        return JA_CONS[JA_PAL[label]][1]
    if label in JA_CONS and label:
        return JA_CONS[label][0]
    if label == 'ng':
        return 'N'
    return None


def ja_token(tok):
    """One alias token -> (kind, phonemes). kind: 'sil', 'syl', 'cons', 'skip'."""
    if tok in ('-', 'R', '_', 'sil', 'Sil'):
        return 'sil', ['Sil']
    if tok in ('息', '吸', 'br', 'breath', '・', '?'):
        return 'skip', []
    if re.search(r'[぀-ヿ]', tok):
        rom = kana_to_romaji(tok.replace('・', ''))
        if not rom or len(rom) != 1 or '・' in tok:
            return 'skip', []
        tok = rom[0]
        if tok == 'n':
            return 'syl', ['N\\']
    elif tok.lower() in ('n', 'nn'):
        return 'cons', ['n']          # romaji n: VC consonant on the right, ん elsewhere
    syl = ja_syllable(tok)
    if syl is not None:
        return 'syl', syl
    c = ja_consonant(tok)
    if c is not None:
        return 'cons', [c]
    return 'skip', []


def nasal_before(nxt):
    """Context variant of ん given the following phoneme."""
    if nxt in ('k', "k'", 'g', "g'", 'N'):
        return 'N'
    if nxt in ('p', "p'", 'b', "b'", 'm', "m'"):
        return 'm'
    if nxt in ('t', "t'", 'd', "d'", 'ts', 'tS', 'dz', 'dZ', 'n', 'J', '4', "4'", 'z', 'Z'):
        return 'n'
    if nxt in ('j', 'i'):
        return "N'"
    return 'N\\'


def ja_alias(alias):
    """Japanese alias -> phoneme list, or None to skip."""
    toks = alias.split()
    if not toks or len(toks) > 2:
        return None
    parsed = [ja_token(t) for t in toks]
    if any(k == 'skip' for k, _ in parsed):
        return None
    if len(parsed) == 1:
        kind, ph = parsed[0]
        if kind == 'sil':
            return None
        if kind == 'cons':             # lone consonant alias (e.g. CVVC "k"); only "n" means ん
            return ['N\\'] if ph == ['n'] else None
        return ph                      # CV / V / N
    (k0, p0), (k1, p1) = parsed
    if k0 == 'sil' and k1 == 'sil':
        return None
    if k0 == 'cons':                   # "n ka" (ん + CV) or a consonant-led split
        left = ['N\\'] if p0 == ['n'] else p0
    elif k0 == 'syl':
        left = [p0[-1]]                # VCV left side: the vowel (or N) it ends with
    else:
        left = ['Sil']
    right = p1
    seq = left + right
    # context rules
    if seq[0] == 'N\\' and len(seq) > 1:
        seq[0] = nasal_before(seq[1])
    for i in range(1, len(seq)):
        prev = seq[i - 1]
        if seq[i] in JA_INITIAL and (prev == 'Sil' or prev in NASAL):
            seq[i] = JA_INITIAL[seq[i]]
    return seq


# ------------------------------------------------------------------ English (Arpasing)

ARPA = {
    'aa': 'Q', 'ae': '{', 'ah': 'V', 'ao': 'O:', 'aw': 'aU', 'ax': '@', 'ay': 'aI', 'eh': 'e', 'er': '@r',
    'ey': 'eI', 'ih': 'I', 'iy': 'i:', 'ow': '@U', 'oy': 'OI', 'uh': 'U', 'uw': 'u:',
    'b': 'b', 'ch': 'tS', 'd': 'd', 'dh': 'D', 'dx': '4', 'f': 'f', 'g': 'g', 'hh': 'h', 'jh': 'dZ',
    'k': 'k', 'l': 'l', 'm': 'm', 'n': 'n', 'ng': 'N', 'p': 'p', 'r': 'r', 's': 's', 'sh': 'S',
    't': 't', 'th': 'T', 'v': 'v', 'w': 'w', 'y': 'j', 'z': 'z', 'zh': 'Z', 'q': '?',
}


def en_arpa_alias(alias):
    toks = alias.lower().split()
    if not 2 <= len(toks) <= 3:
        return None
    seq = []
    for t in toks:
        if t in ('-', 'r', '_'):
            seq.append('Sil')
        elif t in ARPA:
            seq.append(ARPA[t])
        else:
            return None
    if seq.count('Sil') == len(seq) or 'Sil' in seq[1:-1]:
        return None
    return seq


# X-SAMPA-style English banks (e.g. Kasane Teto English): syllables written together ("dZu", "DaI", "skl"),
# "-" for silence, a trailing "-" for a word end ("oU l-").
XSAMPA_EN = {
    'tS': 'tS', 'dZ': 'dZ', 'aI': 'aI', 'aU': 'aU', 'OI': 'OI', 'eI': 'eI', 'oU': '@U',
    '@': '@', '3': '@r', 'A': 'Q', 'a': 'Q', 'E': 'e', 'e': 'e', 'I': 'I', 'i': 'i:', 'O': 'O:', 'o': '@U',
    'U': 'U', 'u': 'u:', 'V': 'V', '{': '{',
    'b': 'b', 'd': 'd', 'g': 'g', 'p': 'p', 't': 't', 'k': 'k', 'f': 'f', 'v': 'v', 'T': 'T', 'D': 'D',
    's': 's', 'z': 'z', 'S': 'S', 'Z': 'Z', 'h': 'h', 'm': 'm', 'n': 'n', 'N': 'N', 'l': 'l', 'r': 'r',
    'w': 'w', 'j': 'j',
}
_XS_KEYS = sorted(XSAMPA_EN, key=len, reverse=True)


def _xsampa_token(tok):
    out, i = [], 0
    while i < len(tok):
        for k in _XS_KEYS:
            if tok.startswith(k, i):
                out.append(XSAMPA_EN[k])
                i += len(k)
                break
        else:
            return None
    return out


def en_xsampa_alias(alias):
    seq = []
    for tok in alias.split():
        if tok in ('-', 'R', '_'):
            seq.append('Sil')
            continue
        end = tok.endswith('-') and len(tok) > 1
        ph = _xsampa_token(tok[:-1] if end else tok)
        if not ph:
            return None
        seq += ph + (['Sil'] if end else [])
    if len(seq) == 1:
        return seq if seq[0] in VOWELS else None      # a lone held vowel: stationary material
    if seq.count('Sil') == len(seq) or 'Sil' in seq[1:-1]:
        return None
    return seq


def en_alias(alias):
    return en_arpa_alias(alias) or en_xsampa_alias(alias)


# ------------------------------------------------------------------ language detection

def detect_language(aliases):
    ja = en = xs = 0
    for a in aliases:
        if re.search(r'[぀-ヿ]', a):
            ja += 1
            continue
        toks = a.lower().split()
        if toks and all(t in ARPA or t == '-' for t in toks) and any(t in ARPA and len(t) == 2 for t in toks):
            en += 1
        elif ja_alias(a) is not None:
            ja += 1
        elif en_xsampa_alias(a) is not None:
            xs += 1
    return 'en' if en + xs > ja else 'ja'


def alias_to_phonemes(alias, lang):
    return en_alias(alias) if lang == 'en' else ja_alias(alias)


_EXTRA_V = {'a': 'a', 'i': 'i', 'u': 'M', 'e': 'e', 'o': 'o', 'n': 'N\\',
            'あ': 'a', 'い': 'i', 'う': 'M', 'え': 'e', 'お': 'o', 'ん': 'N\\'}


def extra_alias(alias, extra_folder=False):
    """UTAU extras -> phonemes, or None. "a R" vowel ending, "a R息" vowel + breath out, "a R吸" vowel,
    pause, breath in, "息1" / "breath2" (or "b1" inside an extras folder) breaths, "巻" / "rr" rolled r."""
    a = alias.strip().replace('ｘ', 'x')
    m = re.fullmatch(r'([aiueonあいうえおん])\s*R\s*(息|吸)?\s*[↑↓]?\d*', a)
    if m:
        v = _EXTRA_V[m.group(1)]
        if m.group(2) == '息':
            return [v, 'brE', 'Sil']
        if m.group(2) == '吸':
            return [v, 'Sil', 'brI', 'Sil']
        return [v, 'Sil']
    m = re.fullmatch(r'(?:-\s*)?(?:息|ブレス|breath|br)\s*([1-5])?', a, re.I)
    if m is None and extra_folder:
        m = re.fullmatch(r'b([1-5])', a)
    if m:
        return ['Sil', 'br%s' % (m.group(1) or '1'), 'Sil']
    if re.fullmatch(r'(?:-\s*)?(?:x?巻(?:き舌)?|rr+|trill)', a, re.I):
        return ['Sil', 'rr', 'Sil']
    return None
