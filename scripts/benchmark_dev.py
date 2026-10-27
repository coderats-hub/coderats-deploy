#!/usr/bin/env python3
"""Conservative, read-only HTTP benchmark; only explicitly allowlisted public DEV health endpoints.
No authentication, private data, writes, or production load tests.
"""
import argparse, concurrent.futures, json, math, statistics, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
def pct(v,p):
    v=sorted(v)
    return round(v[math.ceil(len(v)*p/100)-1],2) if v else None
def benchmark(url, count, timeout):
    samples=[]; errors=0; status={}
    for _ in range(count):
        started=time.perf_counter()
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"CodeRats-Benchmark/1.0","Cache-Control":"no-cache"})
            with urllib.request.urlopen(req,timeout=timeout) as response:
                code=response.status;response.read(8192)
        except urllib.error.HTTPError as exc: code=exc.code
        except (urllib.error.URLError,TimeoutError,ValueError): code=0
        samples.append((time.perf_counter()-started)*1000)
        status[str(code)]=status.get(str(code),0)+1
        if not 200<=code<400:errors+=1
        time.sleep(0.15)
    return {"count":count,"errors":errors,"error_rate_pct":round(100*errors/count,2),"p50_ms":pct(samples,50),"p95_ms":pct(samples,95),"mean_ms":round(statistics.mean(samples),2),"statuses":status}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--base-url",required=True)
    p.add_argument("--service",choices=["web","api","grader"],required=True)
    p.add_argument("--requests",type=int,default=15)
    p.add_argument("--output",default="benchmark-results.json")
    args=p.parse_args()
    if not 3<=args.requests<=30: p.error("requests must be between 3 and 30")
    u=urllib.parse.urlparse(args.base_url)
    if u.scheme!="https" or u.username or u.password or u.query or u.fragment or u.path not in ("","/"):
        p.error("HTTPS origin only, without credentials, path or query")
    hostname=(u.hostname or "").lower()
    if not (hostname.endswith(".azurecontainerapps.io") or hostname=="coderats.club"):
        p.error("Host not allowlisted")
    if hostname=="coderats.club":
        p.error("Production custom domain excluded; use verified DEV FQDN")
    path={"web":"/health","api":"/actuator/health","grader":"/health"}[args.service]
    target=args.base_url.rstrip("/")+path
    before=benchmark(target,3,8)
    if before["errors"]:sys_exit("DEV preflight health failed; benchmark aborted")
    results=benchmark(target,args.requests,8)
    result={"schemaVersion":1,"generated_at":datetime.now(timezone.utc).isoformat(),"service":args.service,"scenario":"read_only_health","environment":"dev","request_path":path,"results":results}
    open(args.output,"w").write(json.dumps(result,indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k!="generated_at"},indent=2))
    if results["errors"]:raise SystemExit("Benchmark experienced failed health requests")
def sys_exit(s):raise SystemExit(s)
if __name__=="__main__":main()
