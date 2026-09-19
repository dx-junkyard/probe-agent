"""IID acceptance: isolation, review, atomic application, durable extraction."""
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import assistant_discussion, interview_discussion_jobs as jobs
from app.db import get_conn


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('PROBE_DB_PATH', str(tmp_path/'iid.db'))
    monkeypatch.setenv('CONTROL_ADMIN_USERNAME','root')
    monkeypatch.setenv('CONTROL_ADMIN_PASSWORD','secret')
    monkeypatch.setenv('LLM_PROVIDER','mock')
    for name in ('CONTROL_API_KEYS','INTELLIGENCE_LLM_PROVIDER','INTELLIGENCE_LLM_MODEL','OPENAI_API_KEY'):
        monkeypatch.delenv(name,raising=False)
    import threading
    monkeypatch.setattr(jobs,'start_worker',lambda:threading.Event())
    from app.main import app
    with TestClient(app) as client:
        response=client.post('/auth/login',json={'username':'root','password':'secret'})
        token=response.cookies.get('probe_session')
        base={'Authorization':f'Bearer {token}'}
        system=client.post('/systems',json={'name':'iid','environment':'test'},headers=base).json()['id']
        headers={**base,'X-Probe-System-Id':str(system)}
        with get_conn() as conn:
            snap=conn.execute("INSERT INTO repository_snapshots(system_id,repo_path,commit_sha,status,created_at) VALUES(?,'/tmp/test','aaa','ready',?)",(system,time.time())).lastrowid
            sids=[]
            for name in ('対象ユーザー','運用フロー'):
                content=json.dumps({'system_purpose':[{'name':'利用者支援','summary':'管理者が利用する'}]})
                sid=conn.execute("INSERT INTO interview_session(system_id,snapshot_id,title,current_understanding,created_at,updated_at) VALUES(?,?,?,?,?,?)",(system,snap,name,content,time.time(),time.time())).lastrowid
                conn.execute("INSERT INTO understanding_revision(system_id,session_id,snapshot_id,current_understanding,created_at,status) VALUES(?,?,?,?,?,'candidate')",(system,sid,snap,content,time.time()))
                sids.append(sid)
        thread=assistant_discussion.resolve_or_create_thread(system,scope='entity',screen_id='interview',target_kind='interview_session',target_ref=str(sids[0]))['thread']['id']
        yield client,headers,system,sids,thread


def post(client,headers,path,body=None,status=200):
    r=client.post(path,headers=headers,json={'request_id':uuid.uuid4().hex,**(body or {})})
    assert r.status_code==status,r.text
    return r.json()


def manual(env,text='現場担当者が毎日利用する'):
    c,h,_,s,_=env
    return post(c,h,f'/interview/sessions/{s[0]}/discussion-contribution-drafts',{'kind':'supplement','text':text},201)


def edit(env,item,targets,text=None):
    c,h,_,_,_=env
    return post(c,h,f"/assistant/interview-candidates/{item['id']}/revisions",{'expected_revision':item['revision'],'kind':item['kind'],'text':text or item['text'],'selected_targets':targets})


def save(env,item,request_id=None):
    c,h,_,_,tid=env
    body={'items':[{'candidate_id':item['id'],'expected_revision':item['revision']}]}
    if request_id: body['request_id']=request_id
    return post(c,h,f'/assistant/discussion-threads/{tid}/interview-contributions',body)


def test_manual_review_multi_interview_and_retry(setup):
    c,h,system,s,tid=setup
    item=manual(setup)
    item=edit(setup,item,[{'session_id':sid,'target_kind':'session'} for sid in s])
    result=save(setup,item,'repeat')
    assert save(setup,item,'repeat')==result
    for sid in s:
        records=c.get(f'/interview/sessions/{sid}/discussion-contributions',headers=h).json()['items']
        assert len(records)==1
        assert records[0]['thread_id']==tid
        assert records[0]['text']==item['text']
    with get_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM system_understanding_head WHERE system_id=?',(system,)).fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM interview_discussion_contribution').fetchone()[0]==1


def test_claim_preview_atomic_apply_and_no_head_change(setup):
    c,h,system,s,_=setup
    targets=c.get(f'/interview/sessions/{s[0]}/discussion-targets',headers=h).json()['targets']
    claim=next(t['ref'] for t in targets if t['ref']['target_kind']=='claim')
    item=edit(setup,manual(setup),[claim])
    save(setup,item)
    lid=c.get(f'/interview/sessions/{s[0]}/discussion-contributions',headers=h).json()['items'][0]['id']
    p=post(c,h,f'/interview/sessions/{s[0]}/discussion-applications/preview',{'contribution_link_ids':[lid]})
    assert p['items'][0]['before']=='管理者が利用する'
    body={'request_id':'apply-once','preview_token':p['preview_token'],'selected_link_ids':[lid]}
    applied=post(c,h,f'/interview/sessions/{s[0]}/discussion-applications',body)
    assert post(c,h,f'/interview/sessions/{s[0]}/discussion-applications',body)==applied
    with get_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM understanding_revision WHERE session_id=?',(s[0],)).fetchone()[0]==2
        assert conn.execute('SELECT COUNT(*) FROM system_understanding_head WHERE system_id=?',(system,)).fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM interview_discussion_revision_source').fetchone()[0]==1


def test_stale_multi_save_rolls_back_everything(setup):
    c,h,_,s,tid=setup
    a=manual(setup)
    b=edit(setup,manual(setup,'別の補足'),[{'session_id':s[1]}])
    with get_conn() as conn:
        conn.execute("UPDATE interview_session SET user_intent='changed' WHERE id=?",(s[1],))
    post(c,h,f'/assistant/discussion-threads/{tid}/interview-contributions',{'items':[{'candidate_id':a['id'],'expected_revision':1},{'candidate_id':b['id'],'expected_revision':b['revision']}]},409)
    with get_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM interview_discussion_contribution').fetchone()[0]==0


def test_unknown_cross_system_and_closed_targets(setup):
    c,h,system,s,tid=setup
    item=manual(setup)
    post(c,h,f"/assistant/interview-candidates/{item['id']}/revisions",{'expected_revision':1,'kind':'supplement','text':'x','selected_targets':[{'session_id':99999}]},404)
    other=c.post('/systems',headers=h,json={'name':'other','environment':'test'}).json()['id']
    foreign={**h,'X-Probe-System-Id':str(other)}
    assert c.get(f'/assistant/discussion-threads/{tid}/interview-candidates',headers=foreign).status_code==404
    with get_conn() as conn: conn.execute("UPDATE interview_session SET status='closed' WHERE id=?",(s[0],))
    post(c,h,f'/assistant/discussion-threads/{tid}/interview-contributions',{'items':[{'candidate_id':item['id'],'expected_revision':1}]},409)


def test_candidate_cas_and_rejected_not_saved(setup):
    c,h,_,_,tid=setup
    item=manual(setup)
    edit(setup,item,[])
    post(c,h,f"/assistant/interview-candidates/{item['id']}/revisions",{'expected_revision':1,'kind':'supplement','text':'lost edit'},409)


def test_extraction_sources_and_no_auto_save(setup,monkeypatch):
    c,h,system,s,tid=setup
    with get_conn() as conn:
        turn=assistant_discussion.append_turn(conn,system_id=system,thread_id=tid,role='user',content='現場担当者が毎日使っています',decision_method='manual')
    def reason(inp,kind):
        return jobs.Extraction(candidates=[jobs.Finding(kind='supplement',text='現場担当者が毎日利用する',sources=[jobs.Source(turn_id=turn['id'],quote=turn['content'])],target_ids=[next(iter(inp['targets']))])])
    monkeypatch.setattr(jobs,'reason',reason)
    job=post(c,h,f'/assistant/discussion-threads/{tid}/interview-extractions',{'through_turn_number':turn['turn_number']},202)
    jobs.run_one(job['id'])
    state=c.get(f"/assistant/interview-extractions/{job['id']}",headers=h).json()
    assert state['state']=='succeeded',state
    items=c.get(f'/assistant/discussion-threads/{tid}/interview-candidates',headers=h).json()['items']
    assert len(items)==1 and items[0]['targets'][0]['selection']=='suggested'
    assert c.get(f'/interview/sessions/{s[0]}/discussion-contributions',headers=h).json()['items']==[]
    jobs.run_one(job['id'])
    assert len(c.get(f'/assistant/discussion-threads/{tid}/interview-candidates',headers=h).json()['items'])==1


def test_fabricated_source_fails_closed(setup,monkeypatch):
    c,h,system,_,tid=setup
    with get_conn() as conn:
        t=assistant_discussion.append_turn(conn,system_id=system,thread_id=tid,role='user',content='本当ですか？',decision_method='manual')
    monkeypatch.setattr(jobs,'reason',lambda *_:jobs.Extraction(candidates=[jobs.Finding(kind='supplement',text='捏造',sources=[jobs.Source(turn_id=t['id'],quote='違う発言')])]))
    job=post(c,h,f'/assistant/discussion-threads/{tid}/interview-extractions',{'through_turn_number':t['turn_number']},202)
    jobs.run_one(job['id'])
    assert c.get(f"/assistant/interview-extractions/{job['id']}",headers=h).json()['error_code']=='invalid_source'
    assert c.get(f'/assistant/discussion-threads/{tid}/interview-candidates',headers=h).json()['items']==[]


def test_reviewer_consumes_contributions_as_reports(setup):
    from app.interview_discussion import context_contributions
    c,h,system,s,_=setup
    save(setup,manual(setup))
    with get_conn() as conn:
        context=context_contributions(conn,system,s[0])
    assert context['provenance']=='human_reviewed_report_not_canonical_fact'
    assert len(context['items'])==1


def test_interview_turn_retry_persists_only_one_pair(setup):
    c,h,system,s,tid=setup
    body={'screen_id':'interview','thread_id':tid,'client_turn_id':'turn-once','question':'対象ユーザーについて教えて'}
    r=c.post('/assistant/ask',headers=h,json=body)
    assert r.status_code==200,r.text
    again=c.post('/assistant/ask',headers=h,json=body)
    assert again.status_code==200,again.text
    assert again.json()==r.json()
    with get_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM assistant_discussion_turn WHERE thread_id=?',(tid,)).fetchone()[0]==2
    bad=c.post('/assistant/ask',headers=h,json={**body,'question':'違う内容'})
    assert bad.status_code==409


@pytest.mark.parametrize('kind',['qa','intent_item'])
def test_existing_answer_and_intent_application(setup,kind):
    c,h,system,s,tid=setup
    with get_conn() as conn:
        if kind=='qa':
            from app.routes.interview import _insert_qa_row
            row_id=_insert_qa_row(conn,s[0],system,question_text='誰が使う？',question_category='general',
                question_source='manual',hypothesis=None,evidence_refs=[],now=time.time())
        else:
            from app.routes.interview_intent import _insert_item
            row_id=_insert_item(conn,s[0],system,field='goal',value_text='旧目的',status='confirmed',origin='user',decision_method='manual',now=time.time())
    item=edit(setup,manual(setup),[{'session_id':s[0],'target_kind':kind,'row_id':row_id}])
    save(setup,item)
    lid=c.get(f'/interview/sessions/{s[0]}/discussion-contributions',headers=h).json()['items'][0]['id']
    p=post(c,h,f'/interview/sessions/{s[0]}/discussion-applications/preview',{'contribution_link_ids':[lid]})
    out=post(c,h,f'/interview/sessions/{s[0]}/discussion-applications',{'preview_token':p['preview_token'],'selected_link_ids':[lid]})
    with get_conn() as conn:
        table='interview_qa' if kind=='qa' else 'interview_intent_item'
        new=conn.execute(f'SELECT * FROM {table} WHERE id=?',(out['applications'][0]['domain_ref'],)).fetchone()
        assert new['status']==('answered' if kind=='qa' else 'confirmed')
        assert conn.execute('SELECT COUNT(*) FROM interview_discussion_outbox').fetchone()[0]==1
