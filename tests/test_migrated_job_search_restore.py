"""使用本机隔离组织验证兼容快照回填与重复执行。"""
import json
import os
from uuid import uuid4

import psycopg
from sqlalchemy import text

from scripts.restore_migrated_job_search_history import restore_history, _compatible_text
from test_job_search_repository_postgres import store,create


def test_restore_migrated_card_and_versions_is_transactional_and_preserves_new_ui(store):
    repo,actor,conversation,message=store
    batch=create(store,'合成公司')
    repo.save_page(actor,conversation,batch['id'],{'request_id':str(uuid4()),'page':1,'jobs':[{'id':'job','title':'合成岗位'}]})
    turn=uuid4()
    card={'type':'job_search','version':2,'conditions':{'city':'杭州','keyword':'合成公司'},'result_summary':{'jobs':[{'title':'合成岗位'}]}}
    with repo._database.transaction() as c:
        snapshot={'batch':c.execute(text('SELECT to_jsonb(t) FROM career_assistant.job_search_batches t WHERE id=:id'),{'id':batch['id']}).scalar_one(),
                  'jobs':c.execute(text('SELECT to_jsonb(t) FROM career_assistant.job_search_job_versions t WHERE batch_id=:id'),{'id':batch['id']}).scalars().all()}
        c.execute(text('DELETE FROM career_assistant.job_search_batches WHERE id=:id'),{'id':batch['id']})
        c.execute(text("INSERT INTO career_assistant.agent_turns(id,conversation_id,actor_id,requested_selection_mode,status) VALUES (:id,:conversation,:actor,'platform_default','succeeded')"),{'id':turn,'conversation':conversation,'actor':actor})
        c.execute(text('UPDATE career_assistant.messages SET turn_id=:turn,ui_payload=NULL,content_text=:body WHERE id=:id'),{'turn':turn,'id':message,'body':_compatible_text('原始回复',card)})
        metadata={'migration_history_ui':{str(message):{'ui_payload':card,'original_content_text':'原始回复'}},'migration_job_search_batches':[snapshot]}
        c.execute(text("INSERT INTO career_assistant.agent_turn_payloads(turn_id,input_text,effective_text,request_metadata_json) VALUES (:turn,'','',CAST(:metadata AS jsonb))"),{'turn':turn,'metadata':json.dumps(metadata,ensure_ascii=False)})
    url=os.environ['CAREER_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)
    with psycopg.connect(url) as c:
        result=restore_history(c)
        assert result['cards_restored']==result['batches_restored']==result['job_versions_restored']==1
        c.rollback()
        assert c.execute('SELECT ui_payload FROM career_assistant.messages WHERE id=%s',(message,)).fetchone()[0] is None
    with psycopg.connect(url) as c:
        restore_history(c)
    with psycopg.connect(url) as c:
        assert c.execute('SELECT content_text FROM career_assistant.messages WHERE id=%s',(message,)).fetchone()[0]=='原始回复'
        c.execute("UPDATE career_assistant.messages SET ui_payload=jsonb_set(ui_payload,'{status}','\"selected\"') WHERE id=%s",(message,))
        repeated=restore_history(c)
        assert repeated['cards_restored']==repeated['batches_restored']==repeated['job_versions_restored']==0
        assert c.execute("SELECT ui_payload->>'status' FROM career_assistant.messages WHERE id=%s",(message,)).fetchone()[0]=='selected'
