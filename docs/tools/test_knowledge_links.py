"""Exercise missing feedback links and the observed plan/guidance mismatch."""
from pathlib import Path
import tempfile
import unittest

import knowledge_links as kl


class KnowledgeLinkTests(unittest.TestCase):
    def test_missing_feedback_and_version_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in kl.ENTRANCES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('# Entry\n')
            plan = root / kl.DIRECTORIES[1] / 'plan.md'
            plan.parent.mkdir(parents=True)
            plan.write_text('# IP-20260929-01: plan\n## 観点の選択（手動照合、`probe-agent-ec/0.1`）\n')
            guidance = root / 'improvement/records/guidance.md'
            guidance.parent.mkdir(parents=True)
            guidance.write_text(
                '# G-20260929-01: guidance\n**接続方式:** `probe-agent-ec/0.2`\n'
                '[plan](../../docs/02-challenges-and-decisions/improvement-plans/plan.md)\n'
                '[result](missing.md)\n')
            errors = kl.check(root)[2]
            self.assertTrue(any('version mismatch' in e for e in errors))
            self.assertTrue(any('missing link' in e for e in errors))
            plan.write_text(plan.read_text().replace('0.1', '0.2'))
            (guidance.parent / 'missing.md').write_text('# Result\n')
            self.assertEqual(kl.check(root)[2], [])

    def test_historical_versions_and_nonlocal_links_are_not_current_contracts(self):
        text = ('**接続方式:** `probe-agent-ec/0.3`\n'
                '## 訂正\n旧表記`probe-agent-ec/0.1`\n'
                '[web](https://example.com) [anchor](#section)\n'
                '```markdown\n[example](placeholder.md)\n```\n'
                '[local](result.md#section)\n')
        self.assertEqual(kl.version(text), 'probe-agent-ec/0.3')
        self.assertEqual(list(kl.local_targets(Path('/tmp/guide.md'), text)),
                         [Path('/tmp/result.md').resolve()])


if __name__ == '__main__':
    unittest.main()
