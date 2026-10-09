#!/usr/bin/env python3
"""Port an UTAU voicebank (VCV, CVVC or CV; Japanese, or English Arpasing) to VOCALOID3 DBTool input.

    python utau2vocaloid.py convert "<UTAU bank folder>" "<output folder>" [options]
    python utau2vocaloid.py validate "<output folder>" [--dict ...]
    python utau2vocaloid.py build "<output folder>" "<DB folder>/<Name>" [--name "Singer"]

The output folder holds <name>.wav/.trans/.seg/.as<N> files. Point the DBTool's
"Segmentate Folder" (WavefilePath) at it, then run the DB build steps.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from u2v import convert, dbtool_io, phonemes as P  # noqa: E402

DEVKIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DICTS = {
    'ja': os.path.join(DEVKIT, 'Japanese Dictionary', 'Japanese_Dictionary.txt'),
    'en': os.path.join(DEVKIT, 'English Dictionary', 'english_phonetic_dictionary_20061220.txt'),
}


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    from u2v import __version__
    ap.add_argument('--version', action='version', version='utau2vocaloid ' + __version__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('convert', help='convert an UTAU bank')
    c.add_argument('bank', help='UTAU voicebank folder (searched recursively for oto.ini)')
    c.add_argument('out', help='output folder for the DBTool (will be created)')
    c.add_argument('--lang', choices=['auto', 'ja', 'en'], default='auto')
    c.add_argument('--dict', help='DB phonetic dictionary .txt (or a DB .dat). '
                                  'Default: the devkit dictionary for the language')
    c.add_argument('--groups', help='regex: only use these pitch/colour groups (sub folder / suffix)')
    c.add_argument('--merge-groups', action='store_true',
                   help='treat all selected groups as one sample set (e.g. CV + VCV folders of one pitch)')
    c.add_argument('--keep', type=int, default=1, help='samples kept per unit and group (default 1)')
    c.add_argument('--no-derive', action='store_true',
                   help="don't create [Sil X]/[X Sil] units from silence found around CV/V samples")
    c.add_argument('--no-fill', action='store_true',
                   help="don't fill missing units by relabelling similar ones or muting onsets")
    c.add_argument('--no-splice', action='store_true',
                   help="don't fake missing [V C] / [V V] / [V N] transitions by joining two samples "
                        "(this is what lets a CV bank sing legato)")
    c.add_argument('--colors', choices=['main', 'evec', 'merge'], default='main',
                   help="banks with several voice colours (soft, whisper ...): 'main' converts only the "
                        "main one (default), 'evec' puts them all in one DB, the others with EVEC-style "
                        "phonemes (a#1, k#1 ...), 'merge' treats all colours as one")
    c.add_argument('--no-normalize', action='store_true',
                   help="keep each recording's own loudness (default: match every unit's vowels to the bank's "
                        "usual level so joins don't jump)")
    c.add_argument('--no-extras', action='store_true',
                   help="skip the bank's extras (breaths -> br1..br5, \"a R息\" breath out -> brE, \"a R吸\" breath "
                        "in -> brI, rolled r -> rr, \"a R\" vowel endings)")
    c.add_argument('--avoid', action='append', default=[], metavar='"C V"',
                   help="don't take this unit from its recording; relabel or splice it from others, "
                        "e.g. --avoid \"g' i\" when one recorded unit renders badly")
    c.add_argument('--brighten', action='append', default=[], metavar='PH=DB',
                   help="lift the treble (above 2 kHz) of a phoneme by DB, e.g. --brighten o=3 for a dull o")
    c.add_argument('--no-extend-dict', action='store_true',
                   help="use the dictionary as is (don't add b, b' and p\' to it)")
    v = sub.add_parser('validate', help='check an output folder the way the DBTool parses it')
    v.add_argument('out')
    v.add_argument('--dict')
    v.add_argument('--lang', choices=['ja', 'en'], default='ja')
    b = sub.add_parser('build', help='drive the DBTool: new singer DB from <out>/dictionary.txt + all build steps')
    b.add_argument('out', help='a converted folder (with dictionary.txt)')
    b.add_argument('db', help='new DB path as folder/Name, e.g. "VOCALOID Developer/Goober/Goober"')
    b.add_argument('--name', help='singer name written to singer.inf')
    b.add_argument('--dbtool', default=os.path.join(DEVKIT, 'VocaloidDBTool3.exe'))
    b.add_argument('--language', default='Japanese', choices=['Japanese', 'English', 'Spanish'])
    a = ap.parse_args()

    if a.cmd == 'build':
        from u2v import dbtool_build
        d = dbtool_io.load_dictionary(os.path.join(a.out, 'dictionary.txt'))
        errs = dbtool_io.validate_folder(a.out, d)
        if errs:
            print('the folder has %d problems, run validate first' % len(errs))
            return 1
        failed = dbtool_build.build(a.dbtool, a.out, a.db, a.name, language=a.language)
        print('DB built: %s%s' % (a.db, '' if not failed else ' (%d files not added)' % len(failed)))
        return 1 if failed else 0

    if a.cmd == 'convert':
        conv = convert.Converter(a.bank, a.out, a.lang, None, a.groups, a.keep, not a.no_derive,
                                 a.merge_groups, not a.no_fill, colors=a.colors,
                                 splice=not a.no_splice, normalize=not a.no_normalize,
                                 brighten={k: float(v) for k, v in (b.split('=') for b in a.brighten)},
                                 avoid=a.avoid, extras=not a.no_extras)
        dpath = a.dict or DEFAULT_DICTS.get(conv.lang)
        if dpath and os.path.exists(dpath):
            conv.dict = dbtool_io.load_dictionary(dpath)
            if conv.lang == 'ja' and not a.no_extend_dict:
                conv.dict = dbtool_io.extend_dictionary(conv.dict, P.JA_EXTRA)
            if conv.extras_found:              # breaths, rolled r: phonemes the stock dictionaries lack
                conv.dict = dbtool_io.extend_dictionary(conv.dict, {p: P.EXTRA_DICT[p] for p in conv.extras_found})
            print('dictionary: %s (%d phonemes)' % (dpath, len(conv.dict.voiced)))
        else:
            print('warning: no phonetic dictionary, phonemes are not checked')
        conv.run()
        errs = dbtool_io.validate_folder(a.out, conv.dict_out)
        print('validation: %s' % ('OK' if not errs else '%d problems' % len(errs)))
        for e in errs[:30]:
            print('  ' + e)
        print('report: %s' % os.path.join(a.out, 'report.txt'))
        return 1 if errs else 0

    # the folder's own dictionary.txt is what the DB gets built from (it has b, b', p' and EVEC phonemes)
    own = os.path.join(a.out, 'dictionary.txt')
    dpath = a.dict or (own if os.path.exists(own) else DEFAULT_DICTS.get(a.lang))
    d = dbtool_io.load_dictionary(dpath) if dpath and os.path.exists(dpath) else None
    errs = dbtool_io.validate_folder(a.out, d)
    for e in errs:
        print(e)
    print('OK' if not errs else '%d problems' % len(errs))
    return 1 if errs else 0


if __name__ == '__main__':
    sys.exit(main())
