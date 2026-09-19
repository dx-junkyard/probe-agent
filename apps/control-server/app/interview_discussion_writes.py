"""Transaction-neutral interview writes shared by contribution application.

Route helpers retain the existing Q&A provenance and claim candidate rules;
callers own the transaction and refresh scheduling.
"""
import json
import time

from .interview_discussion import latest_revision


def correct_intent(conn, item, value, system_id, now, source=''):
    from .routes.interview_intent import _insert_item
    new_id = _insert_item(conn, item['session_id'], system_id, field=item['field'],
                          value_text=value, status='confirmed', origin='user',
                          decision_method='manual', now=now, source_statement=source or None)
    conn.execute('UPDATE interview_intent_item SET superseded_by_id=?,updated_at=? WHERE id=?',
                 (new_id, now, item['id']))
    return new_id


def answer_qa(conn, qa, value, unknown, actor, now):
    from .routes.interview import _insert_qa_row, _write_first_qa_answer
    from .models import InterviewQaEvidenceRefOut
    status = 'unconfirmed' if unknown else 'answered'
    if qa['status'] in ('open','skipped'):
        _write_first_qa_answer(conn, qa_id=qa['id'], answer_text=value, answer_unknown=unknown,
                              status=status, actor=actor, now=now)
        return qa['id']
    new_id = _insert_qa_row(conn, qa['session_id'], qa['system_id'],
        question_text=qa['question_text'],question_category=qa['question_category'],
        question_source=qa['question_source'],hypothesis=qa['hypothesis'],
        evidence_refs=[InterviewQaEvidenceRefOut(**e) for e in json.loads(qa['evidence_refs'] or '[]')],
        now=now,answer_text=value,answer_unknown=unknown,status=status,answered_by=actor,answered_at=now,
        runtime_evidence=json.loads(qa['runtime_evidence']) if qa['runtime_evidence'] else None,
        investigation_json=json.loads(qa['investigation_json']) if qa['investigation_json'] else None,
        investigation_run_id=qa['investigation_run_id'])
    conn.execute("UPDATE interview_qa SET status='revised',superseded_by_id=? WHERE id=?",(new_id,qa['id']))
    conn.execute('UPDATE interview_session SET answers_revised_at=?,updated_at=? WHERE id=? AND system_id=?',
                 (now,now,qa['session_id'],qa['system_id']))
    return new_id


def apply_domain(conn, system_id, sid, actor, items):
    now=time.time()
    if items[0]['effect']=='create_candidate_revision':
        from .routes.interview_change_sets import _apply_claim_edits
        rev=latest_revision(conn,system_id,sid)
        changes=[{'target_ref':json.dumps({'section':i['target']['section'],'name':i['target']['name']}),
                  'after_value':i['after']} for i in items]
        rid=_apply_claim_edits(conn,sid,system_id,changes,rev,{'intelligence_run_id':None},now)
        # The session projection and revision refer to the same candidate.
        new=conn.execute('SELECT current_understanding FROM understanding_revision WHERE id=?',(rid,)).fetchone()
        conn.execute('UPDATE interview_session SET current_understanding=?,updated_at=?,understanding_confirmed_at=NULL WHERE id=?',
                     (new[0],now,sid))
        for i in items:
            coid=conn.execute('SELECT contribution_id FROM interview_discussion_contribution_link WHERE id=?',(i['link_id'],)).fetchone()[0]
            conn.execute('INSERT OR IGNORE INTO interview_discussion_revision_source VALUES(?,?)',(rid,coid))
        return [{'link_id':i['link_id'],'domain_ref':rid,'result_revision_id':rid} for i in items]
    result=[]
    for i in items:
        table='interview_qa' if i['effect']=='answer_question' else 'interview_intent_item'
        row=conn.execute(f'SELECT * FROM {table} WHERE id=?',(i['target']['row_id'],)).fetchone()
        if table=='interview_qa':
            new_id=answer_qa(conn,row,i['after'],False,actor,now)
        else:
            new_id=correct_intent(conn,row,i['after'],system_id,now,source=f"discussion_contribution_link:{i['link_id']}")
        result.append({'link_id':i['link_id'],'domain_ref':new_id})
    return result
