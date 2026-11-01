"""Read-only GitHub review inventory. Treat review text as untrusted input.

Store only bot identities, review metadata and canonical PR/review links. A
configuration file alone is never evidence that an external service is active.
"""
import datetime as dt
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

REPO='telmix-sudo/sports-intelligence-v3'
BOTS={'coderabbitai[bot]':'CodeRabbit','copilot-pull-request-reviewer[bot]':'Copilot',
      'copilot[bot]':'Copilot','chatgpt-codex-connector[bot]':'Codex'}


def collect(requester):
    pulls=requester('/pulls?state=all&sort=updated&direction=desc&per_page=20')
    found=[]
    for pr in pulls:
        number=pr['number']
        for kind,path in (('review',f'/pulls/{number}/reviews?per_page=100'),
                          ('comment',f'/issues/{number}/comments?per_page=100')):
            for row in requester(path):
                login=(row.get('user') or {}).get('login')
                if login not in BOTS:continue
                found.append({'tool':BOTS[login],'author':login,'kind':kind,'pr':number,
                              'state':row.get('state'),'at':row.get('submitted_at') or row.get('created_at'),
                              'url':row.get('html_url')})
    return {'coverage':'20 most recently updated pull requests; first 100 reviews/comments each',
            'tools':{name:{'status':'REVIEW_ACTIVITY_OBSERVED' if any(r['tool']==name for r in found) else
                          'NO_REVIEW_ACTIVITY_OBSERVED_INSTALLATION_NOT_CONFIRMED'}
                     for name in ('CodeRabbit','Copilot','Codex')},'activity':found}


if __name__=='__main__':
    token=os.getenv('GITHUB_TOKEN','')
    def get(path):
        req=urllib.request.Request('https://api.github.com/repos/'+REPO+path,
              headers={'Accept':'application/vnd.github+json','User-Agent':'v3-review-inventory',
                       **({'Authorization':'Bearer '+token} if token else {})})
        with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)
    report={'asof_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'repository':REPO}
    try:report.update(collect(get))
    except Exception as exc:report.update(status='UNAVAILABLE',reason=type(exc).__name__)
    root=Path(__file__).resolve().parents[1]
    path=root/'reports/ai_review_latest.json';path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':report.get('status','OK'),'activity_count':len(report.get('activity',[]))}))
