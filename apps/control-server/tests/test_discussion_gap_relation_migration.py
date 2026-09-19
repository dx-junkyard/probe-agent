"""Upgrade PR462's pre-Gap-reference schema without losing child or ACK audit rows."""
import sqlite3
from app import db


def test_gap_relation_upgrade_preserves_children_audit_and_restore(tmp_path):
    path = tmp_path / 'upgrade.db'
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript('CREATE TABLE systems(id INTEGER PRIMARY KEY); CREATE TABLE assistant_discussion_thread(id INTEGER PRIMARY KEY); CREATE TABLE intelligence_runs(id INTEGER PRIMARY KEY); INSERT INTO systems VALUES(1); INSERT INTO assistant_discussion_thread VALUES(1);')
    old = db._ASSISTANT_DISCUSSION_PROPOSAL_DDL.replace(", 'source_ref', 'evidence_ref', 'artifact_link'", '')
    conn.executescript(old)
    conn.execute("INSERT INTO assistant_discussion_proposal(id,system_id,thread_id,screen_id,target_kind,target_ref,created_at) VALUES(1,1,1,'ux-design-studio','ux_requirement','r',1)")
    conn.execute("INSERT INTO assistant_discussion_proposal_item(id,system_id,proposal_id,item_kind,field_name,child_kind,child_key,child_intent,child_order,created_at) VALUES(7,1,1,'field','statement','acceptance_criterion','reserved-7','add',3,1)")
    conn.execute("INSERT INTO assistant_discussion_proposal_prefill(system_id,proposal_id,item_id,form_id,patch_token,created_at) VALUES(1,1,7,'ux_requirement.revision','ack-7',1)")
    before = dict(conn.execute('SELECT * FROM assistant_discussion_proposal_item').fetchone())
    db._migrate_assistant_discussion_proposal_item_children(conn)
    assert dict(conn.execute('SELECT * FROM assistant_discussion_proposal_item').fetchone()) == before
    assert conn.execute('SELECT item_id FROM assistant_discussion_proposal_prefill').fetchone()[0] == 7
    assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    db._migrate_assistant_discussion_proposal_item_children(conn)
    backup = sqlite3.connect(tmp_path / 'backup.db', isolation_level=None)
    conn.backup(backup)
    conn.close()
    restored = sqlite3.connect(path, isolation_level=None)
    restored.row_factory = sqlite3.Row
    backup.backup(restored)
    backup.close()
    db._migrate_assistant_discussion_proposal_item_children(restored)
    restored.execute("INSERT INTO assistant_discussion_proposal_item(system_id,proposal_id,item_kind,relation_kind,created_at) VALUES(1,1,'relation','artifact_link',2)")
    assert restored.execute('SELECT COUNT(*) FROM assistant_discussion_proposal_item').fetchone()[0] == 2
    assert not restored.execute('PRAGMA foreign_key_check').fetchall()
    restored.close()
