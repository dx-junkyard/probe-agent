"""Guardrails for malformed data and misleading aggregation, without app services."""
import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import issue_knowledge as ik


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.entries, self.vocabulary, self.patterns = ik.load()
        self.e = copy.deepcopy(self.entries[0])
        self.path = ik.ROOT / ik.REL / 'entries' / self.e['_path']
        _, self.body = ik.metadata(self.path)

    def validate(self):
        ik.validate_entry(ik.ROOT, self.path, self.e, self.body, self.vocabulary, self.patterns)

    def test_repository_and_generated_index(self):
        self.assertEqual((ik.ROOT / ik.REL / 'index.md').read_text(), ik.render(self.entries, self.vocabulary, self.patterns))

    def test_check_rejects_stale_index_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ik.REL / 'index.md'
            path.parent.mkdir(parents=True)
            path.write_text('stale index')
            with patch.object(ik, 'ROOT', root), patch.object(ik, 'load', return_value=(self.entries, self.vocabulary, self.patterns)), patch('sys.argv', ['issue_knowledge.py', '--check']), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(ik.main(), 1)
            self.assertIn('未同期', err.getvalue())
            self.assertEqual(path.read_text(), 'stale index')

    def test_unknown_cannot_be_confirmed_cause(self):
        self.e['classification']['axes']['processing'] = ['unknown']
        self.e['classification']['axis_confidence']['processing'] = 'low'
        with self.assertRaisesRegex(ValueError, 'unknownは仮説'):
            self.validate()

    def test_sentinel_cannot_be_mixed(self):
        self.e['classification']['axes']['processing'] = ['none', 'logic']
        with self.assertRaisesRegex(ValueError, '空値の混在'):
            self.validate()

    def test_confirmed_review_needs_reviewer(self):
        self.e['classification']['review'] = 'confirmed'
        with self.assertRaisesRegex(ValueError, '分類確定者'):
            self.validate()

    def test_resolved_requires_verification(self):
        self.e['resolution']['verification'] = []
        with self.assertRaisesRegex(ValueError, '検証証拠'):
            self.validate()

    def test_invalid_vocabulary_and_missing_source(self):
        self.e['classification']['axes']['connection'] = ['invented']
        with self.assertRaisesRegex(ValueError, '不正な値'):
            self.validate()
        self.e = copy.deepcopy(self.entries[0])
        self.e['sources'] = ['docs/nonexistent-source.md']
        with self.assertRaisesRegex(ValueError, '参照先がない'):
            self.validate()

    def test_path_escape(self):
        with self.assertRaisesRegex(ValueError, '相対パス'):
            ik.existing(ik.ROOT, '../README.md')

    def test_duplicate_metadata_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'entry.md'
            p.write_text('---\n{"id":"IK-0001","id":"IK-0002"}\n---\n')
            with self.assertRaisesRegex(ValueError, '重複キー'):
                ik.metadata(p)

    def test_same_cause_is_one_vote(self):
        es = [copy.deepcopy(self.entries[0]) for _ in range(3)]
        for i, e in enumerate(es):
            e['id'] = f'IK-{i+1:04}'
            e['classification']['review'] = 'confirmed'
            e['classification']['proposals'] = [{
                'kind':'value', 'target_axis':'connection', 'neighbor_of':['connection.version'],
                'statement':'境界が区別できない', 'confidence':'high'}]
        es[0]['view_of'] = ['IK-0002']
        es[1]['view_of'] = ['IK-0003']
        output = ik.render(es, self.vocabulary, {es[0]['pattern']:'contract-transfer'})
        self.assertIn('highの独立原因 1 / 蓄積中', output)
        self.assertIn('分類確定の独立原因: 1', output)
        es[0]['view_of'] = es[1]['view_of'] = []
        output = ik.render(es, self.vocabulary, {es[0]['pattern']:'contract-transfer'})
        self.assertIn('highの独立原因 3 / 設定候補', output)
        self.assertIn('/ 成立 /', output)

    def test_reevaluation_is_limited_to_uncertain_neighbors(self):
        es = [copy.deepcopy(self.entries[2]) for _ in range(2)]
        es[1]['id'] = 'IK-0099'
        es[0]['classification']['axis_confidence']['connection'] = 'medium'
        es[1]['classification']['axis_confidence']['connection'] = 'high'
        vocabulary = copy.deepcopy(self.vocabulary)
        vocabulary['provisional_values'] = [{'value':'connection.information', 'neighbor_of':['connection.version'], 'introduced_at':'2026-09-19'}]
        output = ik.render(es, vocabulary, {es[0]['pattern']:'version-lineage'})
        queue = next(line for line in output.splitlines() if '再評価:' in line)
        self.assertIn('IK-0003', queue)
        self.assertNotIn('IK-0099', queue)


if __name__ == '__main__':
    unittest.main()
