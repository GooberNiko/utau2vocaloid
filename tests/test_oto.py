"""Regression tests for reading UTAU banks and mapping aliases (no audio needed).

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unicodedata
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import oto, phonemes as P  # noqa: E402


def make_bank(root, folders, prefix_map=None):
    """folders: {relative folder: [(wav name, alias), ...]}; wavs are empty files."""
    for folder, rows in folders.items():
        d = os.path.join(root, folder)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'oto.ini'), 'w', encoding='utf-8') as f:
            for wav, alias in rows:
                f.write('%s=%s,100,50,-200,30,10\n' % (wav, alias))
                open(os.path.join(d, wav), 'wb').close()
    if prefix_map:
        with open(os.path.join(root, 'prefix.map'), 'w', encoding='utf-8') as f:
            f.write(prefix_map)


class ReadBank(unittest.TestCase):
    def read(self, folders, prefix_map=None):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root, folders, prefix_map)
            return oto.read_bank(root)

    def test_folder_pitch_suffix(self):
        es = self.read({'C3': [('a.wav', '- かC3'), ('b.wav', 'a かC3'), ('c.wav', 'かC3')]})
        self.assertEqual(sorted(e.alias for e in es), ['- か', 'a か', 'か'])
        self.assertTrue(all(e.pitch == 'C3' for e in es))

    def test_colour_and_pitch(self):
        es = self.read({'SoftC3': [('a.wav', 'かC3SF'), ('b.wav', 'a かC3SF')],
                        'StandardA#3': [('a.wav', 'かA#3'), ('b.wav', 'a かA#3')]})
        by = {e.group: e for e in es}
        soft = [e for e in es if e.group.startswith('SoftC3')][0]
        std = [e for e in es if e.group.startswith('StandardA#3')][0]
        self.assertEqual((soft.pitch, std.pitch), ('C3', 'A#3'))
        self.assertNotEqual(soft.color, std.color)
        self.assertTrue(by)

    def test_kanji_tag_stripped_kana_kept(self):
        es = self.read({'weak': [('a.wav', '- か弱'), ('b.wav', 'a か弱'), ('c.wav', 'か弱')]})
        self.assertEqual(sorted(e.alias for e in es), ['- か', 'a か', 'か'])
        es = self.read({'v': [('a.wav', 'a あ'), ('b.wav', 'i あ'), ('c.wav', 'u あ')]})
        self.assertEqual(sorted(e.alias for e in es), ['a あ', 'i あ', 'u あ'])

    def test_prefix_map_suffix_is_pitch(self):
        es = self.read({'A4': [('a.wav', 'かF'), ('b.wav', 'a かF')], 'G4': [('a.wav', 'か'), ('b.wav', 'a か')]},
                       prefix_map='C5\t\tF\nC4\t\t\n')
        self.assertTrue(all('F' not in e.color for e in es), [e.color for e in es])
        self.assertEqual(len({e.color for e in es}), 1)

    def test_nfd_file_names(self):
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, 'v')
            os.makedirs(d)
            nfd = unicodedata.normalize('NFD', '_が.wav')
            open(os.path.join(d, nfd), 'wb').close()
            with open(os.path.join(d, 'oto.ini'), 'w', encoding='utf-8') as f:
                f.write('_が.wav=- が,100,50,-200,30,10\n')
            es = oto.read_bank(root)
            self.assertEqual(len(es), 1)
            self.assertTrue(os.path.exists(es[0].wav))


class Phonemes(unittest.TestCase):
    def test_numbering(self):
        self.assertEqual(P.NUMBERING.sub(r'\1', '4. F3/_F3'), 'F3/_F3')
        self.assertEqual(P.NUMBERING.sub(r'\1', '11. CONSONANT RELEASES/-'), 'CONSONANT RELEASES/-')
        self.assertEqual(P.NUMBERING.sub(r'\1', 'A/12. B'), 'A/B')

    def test_ja_alias(self):
        self.assertEqual(P.ja_alias('a か'), ['a', 'k', 'a'])
        self.assertEqual(P.ja_alias('- か'), ['Sil', 'k', 'a'])
        self.assertEqual(P.ja_alias('n か'), ['N', 'k', 'a'])
        self.assertEqual(P.ja_alias('か'), ['k', 'a'])
        self.assertEqual(P.ja_alias('- ん'), ['Sil', 'N\\'])
        self.assertEqual(P.ja_alias('u きゃ'), ['M', "k'", 'a'])
        self.assertEqual(P.ja_alias('- ざ'), ['Sil', 'dz', 'a'])


if __name__ == '__main__':
    unittest.main()
