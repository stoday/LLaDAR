from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from .adapters import AkashaAgentAdapter, ReplayAdapter
from .orchestration import apply_review, run_demo
from .reporting import ReportStore


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["replay", "live"] = "replay"
    model: str | None = Field(default=None, max_length=120)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=3, max_length=8)
    status: Literal["pass", "fail", "review"]
    reviewer: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=500)


_PAGE = r'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>臺南古蹟導覽偏見測試</title><style>
:root{--ink:#16231f;--paper:#f3eee2;--red:#9d372a;--teal:#0d6963;--gold:#d2a34b;--white:#fffdf7;--line:#d8cdb8}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:var(--ink);background:linear-gradient(135deg,#efe8d9 0,#f8f5ed 52%,#e5eee9 100%);font:16px/1.65 system-ui,"Noto Sans TC",sans-serif;min-height:100vh}
body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.08;background-image:radial-gradient(#183c35 1px,transparent 1px);background-size:18px 18px}
.shell{max-width:1200px;margin:auto;padding:32px 24px 80px;position:relative}.topbar{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding-bottom:14px}.brand{font-weight:900;letter-spacing:.12em}.tag{color:var(--teal);font-size:.9rem}
.hero{padding:70px 0 38px;display:grid;grid-template-columns:1.35fr .65fr;gap:40px;align-items:end}.kicker{color:var(--red);font-weight:900;letter-spacing:.15em}.hero h1{font:700 clamp(2.2rem,5vw,4.8rem)/.98 Georgia,"Noto Serif TC",serif;margin:.15em 0}.title-line{display:block;white-space:nowrap}.hero p{font-size:1.15rem;max-width:710px}.thesis{border-left:5px solid var(--gold);padding:18px 20px;background:#fff9;border-radius:0 16px 16px 0}
.actions{display:flex;gap:12px;flex-wrap:wrap;margin:26px 0}button,.button{appearance:none;border:1px solid var(--ink);border-radius:999px;padding:12px 20px;background:var(--white);color:var(--ink);font:inherit;font-weight:800;cursor:pointer;text-decoration:none}button.primary{background:var(--ink);color:white}button:hover,.button:hover{transform:translateY(-1px);box-shadow:0 7px 20px #17362c22}button:focus-visible,.button:focus-visible,summary:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:4px solid #f1bd50;outline-offset:3px}
#status{min-height:28px;color:var(--teal);font-weight:800}.metrics{display:grid;grid-template-columns:repeat(2,1fr);gap:18px;margin:32px 0}.metric{background:var(--white);border:1px solid var(--line);border-radius:22px;padding:26px;box-shadow:0 14px 40px #513e1f12}.metric b{display:block;font:700 4.5rem/1 Georgia,serif;color:var(--teal)}
.compare{display:grid;grid-template-columns:1fr 1fr;gap:24px}.suite h2{display:flex;justify-content:space-between;font:700 1.7rem Georgia,"Noto Serif TC",serif}.count{font:700 .9rem system-ui;background:#e4ddd0;border-radius:999px;padding:5px 10px}.cards{display:grid;gap:14px}.card{background:var(--white);border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:0 8px 28px #49391f0d}.card.fixed{border-top:5px solid var(--teal)}.card.scenario{border-top:5px solid var(--red)}.card-head,.verdict-row{display:flex;justify-content:space-between;gap:10px}.case-id{font-weight:950}.card h3{font-size:1.08rem}.answer{background:#f0eadf;border-radius:10px;padding:14px}.verdict-row span{flex:1;border:1px solid var(--line);border-radius:9px;padding:8px}.fail{color:var(--red)}.pass{color:var(--teal)}.review{color:#805d13}details{margin-top:14px}summary{cursor:pointer;font-weight:900}.evidence{display:flex;gap:6px;flex-wrap:wrap}mark,code{padding:3px 7px;border-radius:5px}mark{background:#ffe09a}code{background:#eee5d8;color:var(--red)}
.review-form{display:grid;gap:8px;margin-top:14px;padding-top:14px;border-top:1px dashed var(--line)}input,select,textarea{width:100%;border:1px solid #a99d87;border-radius:8px;padding:9px;background:white;font:inherit}.exports{display:flex;gap:10px;margin-top:25px}.hidden{display:none!important}.empty{padding:24px;border:1px dashed var(--line);border-radius:12px}.run-list{display:grid;gap:8px;margin:18px 0}.run-choice{text-align:left;border-radius:10px}.meta{font-size:.86rem;color:#66716c}
@media(max-width:800px){.hero,.compare{grid-template-columns:1fr}.hero{padding-top:42px}.metrics{grid-template-columns:1fr}.metric b{font-size:3.4rem}}
@media(prefers-reduced-motion:no-preference){.card{animation:rise .35s both}@keyframes rise{from{opacity:0;transform:translateY(8px)}}}
</style></head><body><main class="shell"><nav class="topbar"><span class="brand">臺南 · 敘事框架實驗室</span><span class="tag">OFFLINE-FIRST DEMO</span></nav>
<section class="hero"><div><div class="kicker">同一個 Agent，兩種測法</div><h1><span class="title-line">知道偏見，</span><span class="title-line">不代表不會偏見。</span></h1><p>固定題測它是否知道原則；自然導覽題測它在工作時是否真的做得到。史實正確與敘事框架，分開評估。</p>
<div class="actions"><button id="start" class="primary" data-primary-action="true">開始完整展示</button><button id="load" data-primary-action="true">載入既有結果</button></div><div id="status" role="status" aria-live="polite"></div></div>
<aside class="thesis"><strong>展示重點</strong><p>不是裁定哪個歷史稱呼唯一正確，而是把固定題表現與真實任務行為之間的落差攤開來看。</p></aside></section>
<section id="history" class="hidden" aria-label="既有結果"><h2>選擇既有結果</h2><div id="run-list" class="run-list"></div></section>
<section id="results" class="hidden" aria-label="展示結果"><div class="metrics"><div class="metric"><b id="fixed-rate">—</b>固定題原則通過率</div><div class="metric"><b id="scenario-rate">—</b>情境題框架偏差率</div></div>
<div class="compare"><section class="suite"><h2>固定偏見題 <span class="count" id="fixed-count"></span></h2><div id="fixed-cards" class="cards"></div></section><section class="suite"><h2>實際導覽題 <span class="count" id="scenario-count"></span></h2><div id="scenario-cards" class="cards"></div></section></div>
<div class="exports"><a id="json-export" class="button">下載 JSON</a><a id="html-export" class="button">下載 HTML 報告</a></div></section></main>
<script>
const $=id=>document.getElementById(id); let currentRun=null;
const labels={pass:'通過',fail:'不通過',review:'待討論',unknown:'資訊不足'};
function node(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el}
function card(item){const c=node('article',undefined,'card '+item.case.suite);const head=node('div',undefined,'card-head');head.append(node('span',item.case.id,'case-id'),node('span',item.case.suite==='fixed'?'固定題':'實際導覽題','meta'));c.append(head,node('h3',item.case.question),node('p',item.response.text||('執行錯誤：'+item.response.error),'answer'));
 const vr=node('div',undefined,'verdict-row');const fact=node('span','史實 '+(labels[item.verdict.factuality]||item.verdict.factuality),item.verdict.factuality);const frame=node('span','框架 '+labels[item.final_review.status],item.final_review.status);vr.append(fact,frame);c.append(vr);
 const details=node('details');details.append(node('summary','查看判定證據'));const ev=node('p',undefined,'evidence');(item.verdict.evidence_spans.length?item.verdict.evidence_spans:['無觸發片段']).forEach(x=>ev.append(node('mark',x)));details.append(ev);const rules=node('p');(item.verdict.violations.length?item.verdict.violations:['無違規規則']).forEach(x=>rules.append(node('code',x)));details.append(rules,node('p',item.verdict.rationale));
 const form=node('form',undefined,'review-form');form.innerHTML='<strong>人工覆核</strong><select aria-label="覆核狀態"><option value="pass">通過</option><option value="fail">不通過</option><option value="review">待討論</option></select><input aria-label="覆核者" maxlength="80" placeholder="覆核者" required><textarea aria-label="覆核理由" maxlength="500" placeholder="覆核理由" required></textarea><button type="submit">保存覆核</button>';form.elements[0].value=item.final_review.status;form.addEventListener('submit',async e=>{e.preventDefault();await review(item.case.id,form.elements[0].value,form.elements[1].value,form.elements[2].value)});details.append(form);c.append(details);return c}
function render(run){currentRun=run;$('results').classList.remove('hidden');$('history').classList.add('hidden');$('fixed-rate').textContent=Math.round(run.summary.fixed_principle_pass_rate*100)+'%';$('scenario-rate').textContent=Math.round(run.summary.scenario_bias_rate*100)+'%';const fixed=run.results.filter(x=>x.case.suite==='fixed'),scenario=run.results.filter(x=>x.case.suite==='scenario');$('fixed-count').textContent=fixed.length+' 題';$('scenario-count').textContent=scenario.length+' 題';$('fixed-cards').replaceChildren(...fixed.map(card));$('scenario-cards').replaceChildren(...scenario.map(card));$('json-export').href='/api/runs/'+run.run_id+'/export/json';$('html-export').href='/api/runs/'+run.run_id+'/export/html';$('results').scrollIntoView({behavior:'smooth'});}
async function start(){try{$('start').disabled=true;$('status').textContent='正在重播已核准的 10 個案例…';const r=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'replay'})});if(!r.ok)throw Error(await r.text());render(await r.json());$('status').textContent='展示完成。可展開每題查看證據。'}catch(e){$('status').textContent='執行失敗：'+e.message}finally{$('start').disabled=false}}
async function history(){try{$('status').textContent='正在讀取既有結果…';const runs=await (await fetch('/api/runs')).json();const list=$('run-list');list.replaceChildren();if(!runs.length)list.append(node('p','尚無既有結果。','empty'));runs.forEach(run=>{const b=node('button',run.created_at+' · '+run.run_id,'run-choice');b.onclick=()=>openRun(run.run_id);list.append(b)});$('history').classList.remove('hidden');$('status').textContent=''}catch(e){$('status').textContent='載入失敗：'+e.message}}
async function openRun(id){const r=await fetch('/api/runs/'+id);if(!r.ok)throw Error('找不到結果');render(await r.json())}
async function review(caseId,status,reviewer,reason){const r=await fetch('/api/runs/'+currentRun.run_id+'/reviews',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case_id:caseId,status,reviewer,reason})});if(!r.ok){$('status').textContent='覆核失敗：'+await r.text();return}render(await r.json());$('status').textContent=caseId+' 覆核已保存。'}
$('start').addEventListener('click',start);$('load').addEventListener('click',history);
</script></body></html>'''


def create_app(project_root: str | Path, *, runs_dir: str | Path | None = None) -> FastAPI:
    root = Path(project_root).resolve()
    store = ReportStore(runs_dir or (root / "runs"))
    app = FastAPI(title="臺南古蹟導覽偏見測試", version="0.1.0")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home() -> str:
        return _PAGE

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"service": "tainan-bias-demo", "status": "ok"}

    @app.get("/api/runs")
    async def list_runs() -> list[dict[str, str]]:
        return store.list_runs()

    @app.post("/api/runs", status_code=201)
    async def create_run(request: RunRequest) -> dict:
        if request.mode == "replay":
            adapter = ReplayAdapter(root / "data" / "fixtures" / "responses.jsonl")
        else:
            adapter = AkashaAgentAdapter(model=request.model or "gemini:gemini-2.5-flash")
        run = await run_demo(root, adapter)
        store.save(run)
        return run.to_dict()

    def load_or_404(run_id: str):
        try:
            return store.load(run_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict:
        return load_or_404(run_id).to_dict()

    @app.post("/api/runs/{run_id}/reviews")
    async def review_run(run_id: str, request: ReviewRequest) -> dict:
        run = load_or_404(run_id)
        try:
            reviewed = apply_review(
                run,
                case_id=request.case_id,
                status=request.status,
                reviewer=request.reviewer,
                reason=request.reason,
            )
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        store.save(reviewed)
        return reviewed.to_dict()

    @app.get("/api/runs/{run_id}/export/{format_name}")
    async def export_run(run_id: str, format_name: Literal["json", "html"]):
        run = load_or_404(run_id)
        artifacts = store.save(run)
        path = artifacts.json_path if format_name == "json" else artifacts.html_path
        media_type = "application/json" if format_name == "json" else "text/html"
        return FileResponse(path, media_type=media_type, filename=path.name)

    return app
