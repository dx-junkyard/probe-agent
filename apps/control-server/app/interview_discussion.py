"""Interest-led interview discussion. See interview-discussion-contributions.md.

This module owns review records, never the canonical Understanding head. All
domain writes and their receipts share the caller's immediate transaction.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from typing import Any

from fastapi import HTTPException

from .db import get_conn, write_transaction
from .interview_discussion_models import Target
from .trace_redaction import redact_text

VERSION = 'interview-discussion-v1'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def clean(text):
    return redact_text(text, field_name='interview_discussion')[0] or ''


def fail(code, status=409):
    raise HTTPException(status_code=status, detail={'code': code})


def session(conn, system_id, session_id, writable=False):
    row = conn.execute('SELECT * FROM interview_session WHERE id=? AND system_id=?',
                       (session_id, system_id)).fetchone()
    if row is None:
        fail('not_found', 404)
    if writable and row['status'] == 'closed':
        fail('session_closed')
    return dict(row)


def thread(conn, system_id, thread_id):
    row = conn.execute('SELECT * FROM assistant_discussion_thread WHERE id=? AND system_id=?',
                       (thread_id, system_id)).fetchone()
    if row is None:
        fail('not_found', 404)
    if row['target_kind'] != 'interview_session':
        fail('unsupported', 422)
    session(conn, system_id, int(row['target_ref']))
    return dict(row)


def latest_revision(conn, system_id, sid):
    row = conn.execute('SELECT * FROM understanding_revision WHERE system_id=? AND session_id=? ORDER BY id DESC LIMIT 1',
                       (system_id, sid)).fetchone()
    return dict(row) if row else None


def resolve(conn, system_id, target: Target):
    s = session(conn, system_id, target.session_id)
    kind = target.target_kind
    ref = target.model_dump(exclude_none=True)
    title, value = s['title'], ''
    if kind == 'session':
        if any((target.row_id, target.revision_id, target.section, target.name)):
            fail('invalid_target', 422)
        content = [s['current_understanding'], s['user_intent'], s.get('base_premise_digest')]
    elif kind in ('qa', 'intent_item'):
        if target.row_id is None or any((target.revision_id, target.section, target.name)):
            fail('invalid_target', 422)
        table = 'interview_qa' if kind == 'qa' else 'interview_intent_item'
        row = conn.execute(f'SELECT * FROM {table} WHERE id=? AND system_id=? AND session_id=?',
                           (target.row_id, system_id, target.session_id)).fetchone()
        if row is None:
            fail('not_found', 404)
        if row['superseded_by_id'] is not None:
            fail('target_stale')
        if kind == 'qa':
            title, value = row['question_text'], row['answer_text'] or ''
            content = [title, value, row['status'], row['answer_unknown']]
        else:
            title, value = row['field'], row['value_text']
            content = [title, value, row['status']]
    else:
        from .change_sets import UNDERSTANDING_SECTIONS
        if target.row_id or not target.revision_id or not target.name or target.section not in UNDERSTANDING_SECTIONS:
            fail('unsupported_target', 422)
        rev = latest_revision(conn, system_id, target.session_id)
        if not rev or rev['id'] != target.revision_id:
            fail('target_stale')
        entries = json.loads(rev['current_understanding'] or '{}').get(target.section, [])
        matches = [e for e in entries if e.get('name') == target.name]
        if len(matches) != 1:
            fail('target_ambiguous' if matches else 'target_unresolvable')
        title, value = target.name, matches[0].get('summary', '')
        content = [rev['id'], matches[0]]
    return {'ref': ref, 'digest': digest(content), 'title': clean(title or str(target.session_id)),
            'value': clean(value), 'session_title': clean(s['title'] or str(s['id']))}


def replay(conn, system_id, actor, request_id, kind, payload):
    row = conn.execute('SELECT * FROM interview_discussion_operation WHERE system_id=? AND actor_id=? AND request_id=?',
                       (system_id, actor, request_id)).fetchone()
    if row:
        if row['operation_kind'] != kind or row['payload_digest'] != digest(payload):
            fail('idempotency_payload_mismatch')
        return json.loads(row['result_json'])
    return None


def receipt(conn, system_id, actor, request_id, kind, payload, result):
    cur = conn.execute('INSERT INTO interview_discussion_operation(system_id,actor_id,request_id,operation_kind,payload_digest,result_json,created_at) VALUES(?,?,?,?,?,?,?)',
                       (system_id, actor, request_id, kind, digest(payload), encoded(result), time.time()))
    return cur.lastrowid


def candidate(conn, system_id, cid):
    row = conn.execute('SELECT * FROM interview_discussion_candidate WHERE id=? AND system_id=?', (cid, system_id)).fetchone()
    if row is None:
        fail('not_found', 404)
    return dict(row)


def require_latest(conn, row, revision):
    newest = conn.execute('SELECT MAX(revision) FROM interview_discussion_candidate WHERE thread_id=? AND candidate_key=?',
                         (row['thread_id'], row['candidate_key'])).fetchone()[0]
    if newest != revision or row['revision'] != revision:
        fail('candidate_revision_mismatch')


def sources(conn, cid):
    return [dict(r) for r in conn.execute('SELECT turn_id,role,quote,quote_digest FROM interview_discussion_candidate_source WHERE candidate_id=?', (cid,))]


def target_rows(conn, cid):
    return [dict(r) for r in conn.execute('SELECT * FROM interview_discussion_target WHERE candidate_id=? ORDER BY id', (cid,))]


def target_view(conn, system_id, row):
    ref = json.loads(row['target_ref_json'])
    result = {'id': row['id'], 'target': ref, 'selection': row.get('selection', 'selected'),
              'reason': row.get('reason', ''), 'origin': row.get('origin', 'manual')}
    try:
        resolved = resolve(conn, system_id, Target(**ref))
        result.update(resolved)
        result['freshness'] = 'current' if resolved['digest'] == row['captured_digest'] else 'stale'
    except HTTPException as exc:
        result.update(freshness='unresolvable' if exc.status_code == 404 else 'stale',
                      reason=exc.detail['code'], title='参照先の再確認が必要です')
    return result


def candidate_view(conn, row):
    return {**row, 'sources': sources(conn, row['id']),
            'targets': [target_view(conn, row['system_id'], r) for r in target_rows(conn, row['id'])]}


def add_candidate(conn, system_id, thread_id, actor, kind, text, targets, source_list,
                  *, extraction_id=None, key=None, revision=1, previous=None, decision='pending', author='manual'):
    cid = conn.execute('INSERT INTO interview_discussion_candidate(system_id,thread_id,extraction_id,candidate_key,revision,kind,text,author_kind,decision,supersedes_candidate_id,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (system_id, thread_id, extraction_id, key or uuid.uuid4().hex, revision, kind,
                        clean(text), author, decision, previous, actor, time.time())).lastrowid
    for src in source_list:
        conn.execute('INSERT INTO interview_discussion_candidate_source(candidate_id,turn_id,role,quote,quote_digest) VALUES(?,?,?,?,?)',
                     (cid, src.get('turn_id'), src['role'], clean(src['quote']), digest(clean(src['quote']))))
    for target, selection, reason, origin in targets:
        resolved = resolve(conn, system_id, target)
        conn.execute('INSERT INTO interview_discussion_target(candidate_id,session_id,target_kind,target_ref_json,captured_digest,reason,origin,selection) VALUES(?,?,?,?,?,?,?,?)',
                     (cid, target.session_id, target.target_kind, encoded(resolved['ref']), resolved['digest'], clean(reason), origin, selection))
    return candidate_view(conn, candidate(conn, system_id, cid))


def edit_candidate(conn, system_id, actor, cid, payload):
    old = candidate(conn, system_id, cid)
    require_latest(conn, old, payload.expected_revision)
    src = sources(conn, cid)
    if clean(payload.text) != old['text']:
        src.append({'role': 'manual', 'quote': payload.text})
    saved = old['decision'] == 'saved'
    targets = [(t, 'selected', '人が選択', 'manual') for t in payload.selected_targets]
    if len({encoded(t.model_dump()) for t in payload.selected_targets}) != len(targets):
        fail('duplicate_target', 422)
    result = add_candidate(conn, system_id, old['thread_id'], actor, payload.kind, payload.text, targets, src,
                           key=None if saved else old['candidate_key'], revision=1 if saved else old['revision']+1,
                           previous=cid, decision=payload.decision)
    if payload.split_held_targets:
        selected = {encoded(t.model_dump()) for t in payload.selected_targets}
        if any(encoded(t.model_dump()) in selected for t in payload.split_held_targets):
            fail('duplicate_target', 422)
        result['held_candidate'] = add_candidate(conn, system_id, old['thread_id'], actor, payload.kind,
            payload.text, [(t, 'selected', '残りを保留', 'manual') for t in payload.split_held_targets], src,
            previous=cid, decision='held')
    return result


def save_contributions(conn, system_id, actor, thread_id, payload):
    thread(conn, system_id, thread_id)
    if len({i.candidate_id for i in payload.items}) != len(payload.items):
        fail('duplicate_candidate', 422)
    result = []
    for selection in payload.items:
        row = candidate(conn, system_id, selection.candidate_id)
        if row['thread_id'] != thread_id:
            fail('not_found', 404)
        require_latest(conn, row, selection.expected_revision)
        if row['decision'] == 'saved':
            result.append(conn.execute('SELECT id FROM interview_discussion_contribution WHERE candidate_id=?', (row['id'],)).fetchone()[0])
            continue
        if row['decision'] == 'rejected':
            fail('candidate_rejected')
        targets = [t for t in target_rows(conn, row['id']) if t['selection'] == 'selected']
        if not targets:
            fail('target_selection_required', 422)
        for t in targets:
            session(conn, system_id, t['session_id'], writable=True)
            r = resolve(conn, system_id, Target(**json.loads(t['target_ref_json'])))
            if r['digest'] != t['captured_digest']:
                fail('target_stale')
        prev = conn.execute('SELECT id FROM interview_discussion_contribution WHERE candidate_id=?',
                            (row['supersedes_candidate_id'],)).fetchone()
        coid = conn.execute('INSERT INTO interview_discussion_contribution(system_id,thread_id,candidate_id,kind,text,source_manifest,supersedes_contribution_id,reviewed_by,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?)',
                            (system_id, thread_id, row['id'], row['kind'], row['text'], encoded(sources(conn,row['id'])), prev[0] if prev else None, actor, time.time())).lastrowid
        for t in targets:
            conn.execute('INSERT INTO interview_discussion_contribution_link(contribution_id,session_id,target_kind,target_ref_json,captured_digest) VALUES(?,?,?,?,?)',
                         (coid,t['session_id'],t['target_kind'],t['target_ref_json'],t['captured_digest']))
        conn.execute("UPDATE interview_discussion_candidate SET decision='saved' WHERE id=?", (row['id'],))
        result.append(coid)
    return {'contribution_ids': result, 'status': 'saved', 'canonical_head_changed': False}


def contributions(conn, system_id, sid, cursor=0, limit=50):
    session(conn, system_id, sid)
    rows = conn.execute('SELECT l.*,c.text,c.kind,c.thread_id,c.source_manifest,c.reviewed_by,c.reviewed_at,c.system_id FROM interview_discussion_contribution_link l JOIN interview_discussion_contribution c ON c.id=l.contribution_id WHERE l.session_id=? AND c.system_id=? AND l.id>? ORDER BY l.id LIMIT ?',
                        (sid,system_id,cursor,limit+1)).fetchall()
    output = []
    for r in rows[:limit]:
        d = dict(r)
        d['target'] = target_view(conn, system_id, d)
        d['sources'] = json.loads(d.pop('source_manifest'))
        app = conn.execute('SELECT * FROM interview_discussion_application WHERE contribution_link_id=?', (d['id'],)).fetchone()
        d['application'] = dict(app) if app else None
        origin = thread(conn,system_id,d['thread_id'])
        d['origin_url'] = f"/interview?session={origin['target_ref']}&discussion={d['thread_id']}"
        output.append(d)
    return {'items':output,'next_cursor':rows[limit-1]['id'] if len(rows)>limit else None}


def context_contributions(conn, system_id, sid):
    page = contributions(conn, system_id, sid, limit=50)
    return {'provenance':'human_reviewed_report_not_canonical_fact',
            'items':[{'id':r['contribution_id'],'kind':r['kind'],'text':r['text'],
                      'sources':r['sources'],'reviewed_by':r['reviewed_by']} for r in page['items']],
            'coverage':'partial' if page['next_cursor'] else 'complete','next_cursor':page['next_cursor']}


def link(conn, system_id, sid, lid):
    row = conn.execute('SELECT l.*,c.text,c.kind,c.system_id FROM interview_discussion_contribution_link l JOIN interview_discussion_contribution c ON c.id=l.contribution_id WHERE l.id=? AND l.session_id=? AND c.system_id=?',
                       (lid,sid,system_id)).fetchone()
    if row is None:
        fail('not_found',404)
    return dict(row)


def make_preview(conn, system_id, actor, sid, ids):
    session(conn,system_id,sid,writable=True)
    if len(set(ids)) != len(ids):
        fail('duplicate_target',422)
    items=[]
    for lid in ids:
        row=link(conn,system_id,sid,lid)
        if row['kind'] not in ('supplement','correction') or row['target_kind']=='session':
            fail('unsupported',422)
        target=Target(**json.loads(row['target_ref_json']))
        r=resolve(conn,system_id,target)
        if r['digest']!=row['captured_digest']:
            fail('target_stale')
        effect={'qa':'answer_question','intent_item':'confirm_intent','claim':'create_candidate_revision'}[target.target_kind]
        items.append({'link_id':lid,'target':r['ref'],'digest':r['digest'],'before':r['value'],
                      'after':row['text'],'effect':effect,'title':r['title']})
    token=secrets.token_urlsafe(32)
    conn.execute('INSERT INTO interview_discussion_preview(token_hash,system_id,actor_id,session_id,manifest_json,expires_at) VALUES(?,?,?,?,?,?)',
                 (digest(token),system_id,actor,sid,encoded(items),time.time()+600))
    return {'preview_token':token,'items':items,'expires_in':600,'canonical_head_changed':False}


def apply_preview(conn,system_id,actor,sid,payload):
    session(conn,system_id,sid,writable=True)
    p=conn.execute('SELECT * FROM interview_discussion_preview WHERE token_hash=? AND system_id=? AND actor_id=? AND session_id=?',
                   (digest(payload.preview_token),system_id,actor,sid)).fetchone()
    if p is None:
        fail('not_found',404)
    if len(set(payload.selected_link_ids))!=len(payload.selected_link_ids):
        fail('duplicate_target',422)
    all_items=json.loads(p['manifest_json'])
    selected=[i for i in all_items if i['link_id'] in payload.selected_link_ids]
    if len(selected)!=len(payload.selected_link_ids):
        fail('invalid_selection',422)
    # A committed application remains recoverable even after its preview expires.
    done=[conn.execute('SELECT * FROM interview_discussion_application WHERE contribution_link_id=?',(i['link_id'],)).fetchone() for i in selected]
    if all(done):
        return {'status':'applied','applications':[dict(r) for r in done],'canonical_head_changed':False}
    if any(done):
        fail('application_conflict')
    if p['expires_at']<time.time():
        fail('preview_expired')
    if len({i['effect'] for i in selected})!=1:
        fail('mixed_effects',422)
    if len({encoded(i['target']) for i in selected})!=len(selected):
        fail('application_conflict')
    for i in selected:
        r=resolve(conn,system_id,Target(**i['target']))
        if r['digest']!=i['digest']:
            fail('target_stale')
    from .interview_discussion_writes import apply_domain
    results=apply_domain(conn,system_id,sid,actor,selected)
    result={'status':'applied','applications':results,'canonical_head_changed':False}
    opid=receipt(conn,system_id,actor,payload.request_id,f'apply:{sid}',payload.model_dump(),result)
    for item,out in zip(selected,results):
        conn.execute('INSERT INTO interview_discussion_application(contribution_link_id,operation_id,domain_kind,domain_ref,result_revision_id,applied_by,applied_at) VALUES(?,?,?,?,?,?,?)',
                     (item['link_id'],opid,item['target']['target_kind'],str(out['domain_ref']),out.get('result_revision_id'),actor,time.time()))
    if selected[0]['effect']!='create_candidate_revision':
        reason='qa_answer' if selected[0]['effect']=='answer_question' else 'intent_update'
        conn.execute('INSERT INTO interview_discussion_outbox(operation_id,session_id,system_id,event_kind) VALUES(?,?,?,?)',(opid,sid,system_id,reason))
    return result
