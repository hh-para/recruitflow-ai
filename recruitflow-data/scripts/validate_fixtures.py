"""验证测试资料的一致性；不代表招聘业务程序已通过测试。"""
import json
from pathlib import Path
from datetime import datetime
p=Path(__file__).resolve().parents[1]/'fixtures'
def load(n): return json.loads((p/n).read_text(encoding='utf-8'))
def main():
 m=load('messages.json'); es=load('expected_results.json')['results']; final=load('expected_final_state.json'); ds=m['deliveries']
 assert m['timezone']=='Asia/Shanghai' and len(ds)==len(es)==20
 seen={}; delivery_ids=set(); previous=None
 for d,e in zip(ds,es):
  assert all(k in d for k in ['delivery_id','source','group_id','message_id','sent_at','sender_id','sender_name','text'])
  assert d['delivery_id'] not in delivery_ids;delivery_ids.add(d['delivery_id'])
  t=datetime.fromisoformat(d['sent_at']);assert t.utcoffset().total_seconds()==28800
  assert previous is None or t>=previous;previous=t
  assert e['delivery_id']==d['delivery_id'] and e['message_id']==d['message_id']
  key=(d['source'],d['group_id'],d['message_id'])
  if key in seen:
   assert {k:v for k,v in d.items() if k!='delivery_id'}==seen[key]
   assert e['expected_action']=='duplicate'
  if e['reference_message_id']:assert any(k[2]==e['reference_message_id'] for k in seen)
  seen[key]={k:v for k,v in d.items() if k!='delivery_id'}
  state=e['expected_state_after'];counts=e['expected_counts_after']; apps={x['application_id']:x for x in state['applications']}; cids={x['candidate_id'] for x in state['candidates']}
  assert len(cids)==counts['candidates'] and len(apps)==counts['applications']
  assert len(state['interviews'])==counts['interviews']
  assert sum(r['status']=='pending' for r in state['reviews'])==counts['open_reviews']
  assert all(a['candidate_id'] in cids for a in apps.values())
  assert all(v['application_id'] in apps for v in state['interviews'])
  if e['expected_action'] in ['duplicate','create_review']:
   prev=es[len(delivery_ids)-2]['expected_state_after']
   assert all(state[k]==prev[k] for k in ['candidates','applications','interviews'])
 assert len(seen)==19
 assert final['counts']==dict(candidates=6,applications=7,interviews=3,business_events=17,open_reviews=0)
 assert len(final['business_history'])==17
 for k in ['candidates','applications','interviews','reviews']:assert final[k]==es[-1]['expected_state_after'][k]
 history=final['business_history'];assert [h['interview_after']['scheduled_at'] for h in history if h['message_id'] in ['M008','M011']]==['2026-11-17T15:00:00+08:00','2026-11-18T14:00:00+08:00']
 assert [h['application_changes']['stage'] for h in history if h['message_id'] in ['M015','M016']]==['一面通过','待定']
 print('PASS: 20次投递、19条唯一消息、6名候选人、7条应聘记录、3条面试、17个业务事件、0个未解决确认；资料一致性校验通过。')
if __name__=='__main__': main()
