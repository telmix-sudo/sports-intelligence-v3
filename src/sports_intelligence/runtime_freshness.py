"""Freshness checks and bounded recovery of the existing free PAPER workflows."""
import datetime as dt
import json
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .odds_provider import parse_utc

UTC=dt.timezone.utc
MAX_AGE_MINUTES=90
RECOVERY_COOLDOWN_MINUTES=60
PENDING={'queued','in_progress','requested','waiting','pending'}


def freshness(root,now,lab_asof=None):
    if now.tzinfo is None:raise ValueError('Timezone required')
    now=now.astimezone(UTC)
    components={}
    for name,path,field in (
        ('scanner','reports/latest.json','generated_at_utc'),
        ('calendar','reports/global_discovery_latest.json','asof_utc'),
        ('laboratory','reports/strategy_lab_latest.json','asof_utc')):
        result={'status':'UNAVAILABLE','asof_utc':None,'age_minutes':None}
        try:
            stamp=lab_asof if name=='laboratory' and lab_asof else json.loads((root/path).read_text())[field]
            age=(now-parse_utc(stamp)).total_seconds()/60
            result.update(asof_utc=stamp,age_minutes=round(age,1),
                          status='FUTURE_TIMESTAMP' if age<0 else 'STALE' if age>MAX_AGE_MINUTES else 'FRESH')
        except (OSError,ValueError,KeyError,TypeError,AttributeError):pass
        components[name]=result
    return {'checked_at_utc':now.isoformat(),'maximum_age_minutes':MAX_AGE_MINUTES,
            'components':components,
            'needs_scanner':any(components[n]['status']!='FRESH' for n in ('scanner','calendar')),
            'needs_laboratory':components['laboratory']['status']!='FRESH'}


def _github_request(method,path,token,payload=None):
    data=json.dumps(payload).encode() if payload is not None else None
    request=Request('https://api.github.com'+path,data=data,method=method,
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
                 'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json'})
    with urlopen(request,timeout=20) as response:
        raw=response.read()
        return json.loads(raw) if raw else {}


def request_recovery(status,repo,token,now,requester=_github_request):
    """One existing workflow dispatch, no new service, token, or odds purchase.

    Check pending runs and a cooldown before dispatch. Never return credentials,
    credential-bearing URLs or remote error bodies to logs or reports.
    """
    if not status['needs_scanner'] and not status['needs_laboratory']:return {'status':'NOT_NEEDED'}
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo or ''):return {'status':'INVALID_REPOSITORY'}
    if not token:return {'status':'MISSING_WORKFLOW_TOKEN'}
    workflow='scanner.yml' if status['needs_scanner'] else 'strategy-lab.yml'
    base='/repos/'+repo+'/actions/workflows/'+workflow
    try:
        runs=requester('GET',base+'/runs?branch=main&per_page=20',token).get('workflow_runs',[])
        for run in runs:
            if run.get('head_branch','main')!='main':continue
            if run.get('status') in PENDING:return {'status':'ALREADY_PENDING','workflow':workflow}
            if run.get('event')=='workflow_dispatch':
                try:age=(now-parse_utc(run['created_at'])).total_seconds()/60
                except (ValueError,KeyError,TypeError):return {'status':'INVALID_RECOVERY_CLOCK','workflow':workflow}
                if age<RECOVERY_COOLDOWN_MINUTES:return {'status':'COOLDOWN','workflow':workflow}
        requester('POST',base+'/dispatches',token,{'ref':'main'})
        return {'status':'REQUESTED','workflow':workflow}
    except HTTPError as exc:return {'status':'GITHUB_REQUEST_FAILED','http_status':exc.code,'workflow':workflow}
    except (URLError,OSError,ValueError,TypeError,AttributeError):return {'status':'GITHUB_REQUEST_FAILED','workflow':workflow}


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path('.'))
    parser.add_argument('--github-output',type=Path)
    parser.add_argument('--recover',action='store_true')
    args=parser.parse_args();now=dt.datetime.now(UTC)
    result=freshness(args.root,now)
    if args.github_output:
        with args.github_output.open('a') as out:
            out.write('needs_scanner='+str(result['needs_scanner']).lower()+'\n')
            out.write('needs_laboratory='+str(result['needs_laboratory']).lower()+'\n')
    if args.recover:
        result['recovery']=request_recovery(result,os.getenv('GITHUB_REPOSITORY',''),os.getenv('GITHUB_TOKEN',''),now)
    print(json.dumps(result,allow_nan=False))
    if args.recover and result['recovery']['status'] in ('INVALID_REPOSITORY','MISSING_WORKFLOW_TOKEN','GITHUB_REQUEST_FAILED','INVALID_RECOVERY_CLOCK'):
        raise SystemExit(1)


if __name__=='__main__':main()
