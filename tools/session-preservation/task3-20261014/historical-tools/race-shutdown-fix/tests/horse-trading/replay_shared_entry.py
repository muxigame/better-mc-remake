"""Replay an archived actual APP trade trace against the exact committed shared main.app.

No Minecraft process is started. Server-generated quote IDs are rebound to fresh
quotes; archived UID, realm, operation IDs, asset/race IDs and proofs stay intact.
Only synthetic balances from the recorded QA are accepted.
"""
from pathlib import Path
import argparse,datetime,hashlib,json,subprocess,sys,os
sys.path.insert(0,r'C:\Users\ranzh\workspace\dev\muxigame\.ops-venv\Lib\site-packages')
import httpx
from trade_fixture import TradeFixture

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--backend-app',type=Path,required=True);parser.add_argument('--commit',required=True)
    parser.add_argument('--trace',type=Path,required=True);parser.add_argument('--snapshot',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();app=args.backend_app.resolve();repo=app.parents[1]
    def git(*values):return subprocess.check_output(['git','-c','safe.directory=*',*values],cwd=repo,text=True,encoding='utf-8').strip()
    assert git('diff',args.commit,'--','server/app')=='','Shared app differs from specified committed source'
    assert 'horse_trading_router' in git('show',args.commit+':server/app/main.py')
    trace=json.loads(args.trace.read_text(encoding='utf-8'));snapshot=json.loads(args.snapshot.read_text(encoding='utf-8'))
    assert trace['privateFixture'] and not trace['productionWrites'] and all(x['event']['uid']==10000 for x in trace['calls'])
    game=next(g for g in snapshot['games'] if g['id']=='horse_racing')
    originals={r['id']:r['receipt'] for r in game['state']['trading']['orders'] if 'receipt' in r}
    result_events=[json.loads(row['event']) for row in trace['tables']['minigame_result_ledger']]
    assert len(result_events)==1 and result_events[0]['uid']==10000 and result_events[0]['score']==10
    lab=args.output.resolve()/('horse-trade-shared-replay-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S'));lab.mkdir(parents=True)
    os.environ['HORSE_TRADE_QA_USE_SHARED_MAIN']='1'
    fixture=TradeFixture(app,app,lab);quote_map={};requests=[];events=[];checks=[];before=None
    try:
        with httpx.Client(base_url=fixture.url,headers={'x-muxi-server-key':fixture.key}) as client:
            def post(route,event):
                response=client.post(route,json=event);requests.append(dict(route=route,operation=event.get('operation'),status=response.status_code));return response
            # The fixture loses five acknowledgements after real commits, exactly
            # as the archived APP run did; its next archived retry must succeed.
            for row in trace['calls']:
                event=json.loads(json.dumps(row['event']))
                if 'quote' in event['payload']:event['payload']['quote']=quote_map[event['payload']['quote']]
                response=post('horse-trades',event)
                assert response.status_code in (200,503),(event['action'],response.status_code,response.text)
                if response.status_code==200:
                    receipt=response.json();original=originals[event['operation']]
                    if event['action']=='quote':
                        quote_map[original['quote']]=receipt['quote'];assert receipt['amount']==original['amount'] and receipt['body']==original['body']
                    for key in ('asset','sale','rental','race','amount','state','refunded','reason'):
                        if key in original:assert receipt.get(key)==original[key],(event['action'],key)
                events.append(event)
            assert len(quote_map)==3 and len(fixture.loss)==5
            before=fixture.checkpoint();checks.append('recorded native buy/ack, sell prepare/commit, rent launch/running/end all accepted by real shared entry')
            assert len(originals)==11
            assert len(before['tables']['horse_trade_ledger'])==len(trace['tables']['horse_trade_ledger'])==12
            assert sum(row['action']=='rental_end_business' for row in before['tables']['horse_trade_ledger'])==1
            for event in events:
                response=post('horse-trades',event);assert response.status_code==200 and response.json().get('replayed')
            after=fixture.checkpoint();assert after['tables']==before['tables'];checks.append('whole actual trade request trace replayed again without new debit, credit, stock or receipt row')
            for _ in range(2):
                response=post('results',result_events[0]);assert response.status_code==200,response.text
            checks.append('recorded actual champion result accepted once by real shared results route; duplicate adds no reward')
            realm=events[0]['realm'];market=client.get('horse-market/10000',params={'realm':realm});requests.append(dict(route='horse-market/10000',status=market.status_code));assert market.status_code==200 and market.json()['balance']=='11165'
            bad=client.get('horse-market/10000',params={'realm':realm},headers={'x-muxi-server-key':'wrong'});requests.append(dict(route='horse-market/10000',status=bad.status_code));assert bad.status_code==401
            checks.append('actual shared service guard rejects invalid key')
        final=fixture.checkpoint();profile=next(r for r in final['tables']['player_profiles'] if r['uid']==10000)
        trade_delta=sum(r['delta'] for r in final['tables']['horse_trade_ledger'] if r['uid']==10000);game_delta=sum(r['points'] for r in final['tables']['minigame_result_ledger'] if r['uid']==10000)
        assert profile['points']==11165==10000+trade_delta+game_delta and profile['balance_cents']==321
        assert trade_delta==1155 and game_delta==10;checks.append('private wallet and separate ledgers reconcile; cash untouched')
        assert len(final['tables']['horse_trade_rentals'])==1 and final['tables']['horse_trade_rentals'][0]['state']=='ENDED'
        assert len(final['tables']['horse_trade_sales'])==1 and final['tables']['horse_trade_sales'][0]['state']=='COMMITTED'
        assert len([a for a in final['tables']['horse_trade_assets'] if a['state']=='OWNED'])==1
        checks.append('one acknowledged owned horse, one committed sold stock, one completed rental; no duplicated asset')
        report=dict(success=True,sharedEntryCommit=args.commit,actualMainEntry=True,sourceSha256=fixture.inputs,archiveTrace=str(args.trace),archiveSnapshot=str(args.snapshot),archiveTraceSha256=hashlib.sha256(args.trace.read_bytes()).hexdigest(),archivedNativeOperations=len(originals),archivedTradeRequests=len(trace['calls']),tradeReplayRequests=len(events)*2,httpRequests=len(requests),lostAfterCommitAcknowledgements=len(fixture.loss),privateBalance=11165,tradeDelta=1155,gameDelta=10,cashCents=321,ledgerReconciles=True,minecraftStarted=False,productionWrites=False,quoteMappingOnly=True,checks=checks,requests=requests,lab=str(lab))
        (lab/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');(args.output/'horse-trade-shared-replay-latest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:v for k,v in report.items() if k not in ('requests','sourceSha256')},ensure_ascii=True))
    finally:fixture.close()

if __name__=='__main__':main()
