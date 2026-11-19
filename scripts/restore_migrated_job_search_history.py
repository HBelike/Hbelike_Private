"""升级后回填旧生产兼容保存的岗位卡片与批次；默认事务预演。"""
from __future__ import annotations

import argparse
import json
import os

import psycopg
from psycopg import sql


def _compatible_text(original, card):
    """还原此前兼容导入器生成的文字，避免覆盖其他正文修改。"""
    lines=[]
    conditions=card.get('conditions') or {}
    condition=' / '.join(str(conditions[k]) for k in ('city','keyword') if conditions.get(k))
    if condition:lines.append('检索条件：'+condition)
    selected=card.get('selected_job') or {}
    if selected:lines.append('已选岗位：'+' · '.join(str(selected[k]) for k in ('title','company','city','salary') if selected.get(k)))
    jobs=(card.get('result_summary') or {}).get('jobs') or []
    if jobs:
        lines.extend(['','岗位结果：'])
        for job in jobs:
            if not isinstance(job,dict):continue
            title=str(job.get('title') or job.get('job_name') or '岗位')
            url=job.get('url') or job.get('job_url')
            title='['+title+']('+str(url)+')' if url else title
            details=' · '.join(str(job[k]) for k in ('company','city','salary') if job.get(k))
            lines.append('- '+title+(' · '+details if details else ''))
    extra='\n'.join(lines)
    return original.rstrip()+'\n\n'+extra if extra else original


def _insert_missing(connection, table, row, keys):
    identifier=sql.Identifier('career_assistant',table)
    predicate=sql.SQL(' AND ').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in keys)
    existing=connection.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t WHERE {}').format(identifier,predicate),tuple(row[k] for k in keys)).fetchone()
    if existing:
        # 已存在的批次可能已经被用户继续使用；仅确认归属，不回写旧快照。
        if table=='job_search_batches':
            assert existing[0]['conversation_id']==row['conversation_id'] and existing[0]['source_message_id']==row['source_message_id']
        else:
            assert existing[0]==row, '不可覆盖已存在的岗位版本'
        return False
    connection.execute(sql.SQL('INSERT INTO {} SELECT * FROM jsonb_populate_record(NULL::{},%s::jsonb)').format(identifier,identifier),(json.dumps(row,ensure_ascii=False),))
    return True


def restore_history(connection):
    result={'cards_restored':0,'cards_preserved':0,'batches_restored':0,'job_versions_restored':0}
    rows=connection.execute("""SELECT turn_id,request_metadata_json FROM career_assistant.agent_turn_payloads
        WHERE request_metadata_json ? 'migration_history_ui' OR request_metadata_json ? 'migration_job_search_batches'
        ORDER BY created_at,turn_id""").fetchall()
    batches={}
    for turn_id,metadata in rows:
        for message_id,stored in metadata.get('migration_history_ui',{}).items():
            current=connection.execute('SELECT content_text,ui_payload FROM career_assistant.messages WHERE id=%s AND turn_id=%s FOR UPDATE',(message_id,turn_id)).fetchone()
            assert current, '卡片来源消息不存在'
            if current[1] is not None:
                result['cards_preserved']+=1
                continue
            original=stored['original_content_text']
            assert current[0] in (original,_compatible_text(original,stored['ui_payload'])), '兼容消息正文已变更，请人工核对'
            connection.execute('UPDATE career_assistant.messages SET content_text=%s,ui_payload=%s::jsonb WHERE id=%s',
                               (original,json.dumps(stored['ui_payload'],ensure_ascii=False),message_id))
            result['cards_restored']+=1
        for snapshot in metadata.get('migration_job_search_batches',[]):
            key=snapshot['batch']['id']
            assert key not in batches or batches[key]==snapshot, '同一批次存在不同快照'
            batches[key]=snapshot
    for snapshot in sorted(batches.values(),key=lambda v:(v['batch']['conversation_id'],v['batch']['batch_no'])):
        result['batches_restored']+=int(_insert_missing(connection,'job_search_batches',snapshot['batch'],['id']))
        for row in snapshot['jobs']:
            result['job_versions_restored']+=int(_insert_missing(connection,'job_search_job_versions',row,['batch_id','job_ref','revision']))
    result['historical_batches']=len(batches)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',help='显式提交；默认执行校验后回滚。')
    args=parser.parse_args()
    with psycopg.connect(os.environ['CAREER_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)) as connection:
        result=restore_history(connection)
        if not args.apply:connection.rollback()
    print(json.dumps({'mode':'apply' if args.apply else 'check','result':'ok',**result},ensure_ascii=True))


if __name__=='__main__':main()
