"""Authenticated routes for interest-led interview discussion."""
from fastapi import APIRouter, Depends, Query

from .. import assistant_discussion, interview_discussion as d, interview_discussion_jobs as jobs
from ..auth import Principal, get_principal, get_system_id
from ..db import get_conn, write_transaction
from ..interview_discussion_models import (
    Apply, EditCandidate, Extract, ManualDraft, Preview, SaveContributions, SearchTargets, Target,
)

router=APIRouter()


def actor(p):
    return p.username or f'user:{p.user_id}'


def mutate(system_id,principal,payload,kind,fn):
    with get_conn() as conn,write_transaction(conn):
        who=actor(principal)
        previous=d.replay(conn,system_id,who,payload.request_id,kind,payload.model_dump())
        if previous is not None:
            return previous
        result=fn(conn,who)
        if not conn.execute('SELECT 1 FROM interview_discussion_operation WHERE system_id=? AND actor_id=? AND request_id=?',(system_id,who,payload.request_id)).fetchone():
            d.receipt(conn,system_id,who,payload.request_id,kind,payload.model_dump(),result)
        return result


@router.get('/assistant/interview-discussion-capabilities')
def capabilities():
    return {'enabled':True,'schema_version':d.VERSION,'target_kinds':['session','qa','intent_item','claim']}


@router.get('/interview/sessions/{sid}/discussion-targets')
def targets(sid:int,system_id:int=Depends(get_system_id)):
    with get_conn() as conn:
        d.session(conn,system_id,sid)
        return {'targets':list(jobs.catalogue(conn,system_id,sid).values())}


@router.post('/interview/sessions/{sid}/discussion-contribution-drafts',status_code=201)
def manual(sid:int,payload:ManualDraft,system_id:int=Depends(get_system_id),principal:Principal=Depends(get_principal)):
    with get_conn() as conn:
        d.session(conn,system_id,sid,writable=True)
    tr=assistant_discussion.resolve_or_create_thread(system_id,scope='entity',screen_id='interview',target_kind='interview_session',target_ref=str(sid),created_by=actor(principal))['thread']
    return mutate(system_id,principal,payload,f'manual:{sid}',lambda conn,who:
        d.add_candidate(conn,system_id,tr['id'],who,payload.kind,payload.text,
            [(Target(session_id=sid),'selected','手入力の起点','manual')],[{'role':'manual','quote':payload.text}]))


@router.post('/assistant/discussion-threads/{tid}/interview-extractions',status_code=202)
def extract(tid:int,payload:Extract,system_id:int=Depends(get_system_id),principal:Principal=Depends(get_principal)):
    return mutate(system_id,principal,payload,f'extract:{tid}',lambda conn,who:jobs.schedule(conn,system_id,who,tid,payload.through_turn_number))


@router.get('/assistant/interview-extractions/{jid}')
def progress(jid:int,system_id:int=Depends(get_system_id)):
    with get_conn() as conn:
        row=conn.execute('SELECT * FROM interview_discussion_extraction WHERE id=? AND system_id=?',(jid,system_id)).fetchone()
        if not row:
            d.fail('not_found',404)
        return {'id':jid,'state':row['state'],'error_code':row['error_code'],**d.json.loads(row['result_json'] or '{}')}


@router.get('/assistant/discussion-threads/{tid}/interview-candidates')
def candidates(tid:int,cursor:int=Query(0,ge=0),system_id:int=Depends(get_system_id)):
    with get_conn() as conn:
        d.thread(conn,system_id,tid)
        rows=conn.execute('SELECT c.* FROM interview_discussion_candidate c WHERE c.thread_id=? AND c.id>? AND NOT EXISTS(SELECT 1 FROM interview_discussion_candidate n WHERE n.thread_id=c.thread_id AND n.candidate_key=c.candidate_key AND n.revision>c.revision) ORDER BY c.id LIMIT 51',(tid,cursor)).fetchall()
        job=conn.execute("SELECT id,state,error_code FROM interview_discussion_extraction WHERE thread_id=? AND job_kind='extract' ORDER BY id DESC LIMIT 1",(tid,)).fetchone()
        return {'items':[d.candidate_view(conn,dict(r)) for r in rows[:50]],'next_cursor':rows[49]['id'] if len(rows)>50 else None,'latest_job':dict(job) if job else None}


@router.post('/assistant/interview-candidates/{cid}/revisions')
def edit(cid:int,payload:EditCandidate,system_id:int=Depends(get_system_id),principal:Principal=Depends(get_principal)):
    return mutate(system_id,principal,payload,f'edit:{cid}',lambda conn,who:d.edit_candidate(conn,system_id,who,cid,payload))


@router.post('/assistant/interview-candidates/{cid}/target-search',status_code=202)
def search(cid:int,payload:SearchTargets,system_id:int=Depends(get_system_id),principal:Principal=Depends(get_principal)):
    return mutate(system_id,principal,payload,f'search:{cid}',lambda conn,who:jobs.search(conn,system_id,who,cid,payload))


@router.post('/assistant/discussion-threads/{tid}/interview-contributions')
def save(tid:int,payload:SaveContributions,system_id:int=Depends(get_system_id),principal:Principal=Depends(get_principal)):
    return mutate(system_id,principal,payload,f'save:{tid}',lambda conn,who:d.save_contributions(conn,system_id,who,tid,payload))


@router.get('/interview/sessions/{sid}/discussion-contributions')
def saved(sid:int,cursor:int=Query(0,ge=0),system_id:int=Depends(get_system_id)):
    with get_conn() as conn:
        return d.contributions(conn,system_id,sid,cursor)


@router.post('/interview/sessions/{sid}/discussion-applications/preview')
def preview(sid:int,payload:Preview,system_id:int=Depends(get_system_id),principal:Principal=Depends(get_principal)):
    return mutate(system_id,principal,payload,f'preview:{sid}',lambda conn,who:d.make_preview(conn,system_id,who,sid,payload.contribution_link_ids))


@router.post('/interview/sessions/{sid}/discussion-applications')
def apply(sid:int,payload:Apply,system_id:int=Depends(get_system_id),principal:Principal=Depends(get_principal)):
    # apply_preview creates the receipt in the same transaction as domain writes.
    return mutate(system_id,principal,payload,f'apply:{sid}',lambda conn,who:d.apply_preview(conn,system_id,who,sid,payload))


@router.get('/assistant/interview-discussion-operations/{request_id}')
def operation(request_id:str,system_id:int=Depends(get_system_id),principal:Principal=Depends(get_principal)):
    with get_conn() as conn:
        row=conn.execute('SELECT result_json FROM interview_discussion_operation WHERE system_id=? AND actor_id=? AND request_id=?',(system_id,actor(principal),request_id)).fetchone()
        if not row:
            d.fail('not_found',404)
        return d.json.loads(row[0])
