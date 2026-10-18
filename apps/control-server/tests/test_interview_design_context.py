"""Design context keeps explicit lineage, freshness and System isolation."""
import json
import sqlite3

from app.db import get_conn
from app.interview_design_context import build_interview_design_context
from test_product_feature import (
    admin_client, _login, _create_system, _headers, _create_feature,
    _add_revision, _create_requirement, _add_requirement_revision,
    _add_requirement_link,
)


def test_design_links_and_staleness_are_read_without_writes(admin_client):
    token = _login(admin_client)
    sid = _create_system(admin_client, token, 'design-context')
    other = _create_system(admin_client, token, 'private-design')
    headers = _headers(token, sid)
    _create_requirement(sid, 'short-checkout')
    _add_requirement_revision(sid, 'short-checkout', statement='迷わず注文できる')
    _create_feature(admin_client, headers, 'checkout')
    _add_revision(admin_client, headers, 'checkout', title='注文する')
    _add_requirement_link(admin_client, headers, 'checkout', 'short-checkout')
    _create_feature(admin_client, _headers(token, other), 'secret-feature')
    with get_conn() as conn:
        before = conn.total_changes
        context = build_interview_design_context(conn, sid)
        assert conn.total_changes == before
    feature = context['sections']['features']['items'][0]
    assert feature['feature_key'] == 'checkout'
    assert feature['design_status'] == 'proposed'
    assert feature['requirement_links'][0]['requirement_key'] == 'short-checkout'
    assert 'secret-feature' not in json.dumps(context)
    from app.assistant_discussion_context import _interview_context
    screen = _interview_context(sid, {})
    assert screen.facts['current_design_context'] == context
    assert any(s['id'] == 'interview_design_context' for s in screen.sources)
    _add_requirement_revision(sid, 'short-checkout', statement='確認して注文できる')
    with get_conn() as conn:
        updated = build_interview_design_context(conn, sid)
    assert updated['sections']['features']['items'][0]['requirement_links'][0]['recheck_state'] == 'stale'
    assert updated['sections']['requirements']['items'][0]['current_revision']['statement'] == '確認して注文できる'


def test_limits_report_omissions_and_preserve_json(admin_client):
    token = _login(admin_client)
    sid = _create_system(admin_client, token, 'bounded-context')
    headers = _headers(token, sid)
    for i in range(7):
        _create_feature(admin_client, headers, f'f-{i}')
    _add_revision(admin_client, headers, 'f-6', statement='長い説明' * 2000)
    with get_conn() as conn:
        context = build_interview_design_context(conn, sid)
    section = context['sections']['features']
    assert section['total'] == 7
    assert section['omitted'] == 7 - len(section['items'])
    assert section['omitted'] >= 2
    assert len(json.dumps(section['items'], ensure_ascii=False)) <= 6000
    assert context['sections']['journeys']['total'] == 0
    assert context['sections']['journeys']['state'] == 'available'


def test_unavailable_is_not_empty():
    conn = sqlite3.connect(':memory:')
    try:
        result = build_interview_design_context(conn, 1)
        assert all(s['state'] == 'unavailable' and s['total'] is None
                   for s in result['sections'].values())
    finally:
        conn.close()
