"""Literal snippet boundaries. These tests never open history stores."""
import unittest

from agent_history.cli import parser, plan
from agent_history.core import snippet


class LiteralContextTests(unittest.TestCase):
    def test_default_boundaries_and_long_query(self):
        for before, query, after in [(0, 'needle', 180), (180, 'needle', 180),
                                     (180, 'needle', 0), (180, 'q' * 700, 180)]:
            with self.subTest(before=before, query_length=len(query), after=after):
                hit = snippet('a' * before + query + 'z' * after, query, 400,
                              default_context=True)
                self.assertEqual(hit['text'], 'a' * min(before, 100) + query + 'z' * min(after, 100))
                self.assertEqual(hit['offset'], max(0, before - 100))

    def test_unicode_and_variants(self):
        body = 'a' * 150 + 'Straße' + 'z' * 150
        for query in ['STRASSE', ('missing', 'STRASSE')]:
            self.assertEqual(snippet(body, query, 400, default_context=True)['text'],
                             'a' * 100 + 'Straße' + 'z' * 100)

    def test_explicit_context_unchanged(self):
        for context in [0, 8, 120, 400]:
            hit = snippet('a' * 180 + 'needle' + 'z' * 180, 'needle', context)
            body = 'a' * 180 + 'needle' + 'z' * 180
            start = max(0, 180 - context // 3)
            self.assertEqual(hit['text'], body[start:start + context])

    def test_cli_distinguishes_omitted_context(self):
        base = ['search', '--host', 'pi', '--store', '.', '--query', 'needle']
        for extra, expected in [([], True), (['--context', '0'], False),
                                (['--context', '120'], False)]:
            args = parser().parse_args(base + extra)
            plan(args)
            self.assertEqual(args.default_context, expected)


if __name__ == '__main__':
    unittest.main()
