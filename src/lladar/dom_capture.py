"""Observe and replay a calibrated visible chat interaction, without private state."""
from __future__ import annotations

import time

from .answer_extraction import ExtractionError
from .browser_target import BrowserResponseLimitExceeded, BrowserDomFailure

DOM_CAPTURE_SCRIPT = r"""
(() => {
  if (globalThis.__lladarDom) return;
  let marker = null, interaction = null, locked = null;
  const rootFor = binding => {
    const roots=binding.kind==='streamlit' ? [...document.querySelectorAll('[data-testid="stApp"]')] :
      [...document.querySelectorAll('[role="log"]')].filter(n=>n.getAttribute('aria-label')===binding.logName);
    return roots.length===1 ? roots[0] : null;
  };
  const visible = node => Boolean(node.getClientRects().length) && getComputedStyle(node).visibility==='visible';
  const messages = binding => {
    const root=rootFor(binding);
    if (!root) return [];
    if (binding.kind==='streamlit') return [...root.querySelectorAll('[data-testid="stChatMessage"]')].map(node => ({
      role: node.querySelector('[data-testid="stChatMessageAvatarUser"]') ? 'user' :
            node.querySelector('[data-testid="stChatMessageAvatarAssistant"]') ? 'assistant' : 'unknown',
      visible: visible(node),
      text: [...node.querySelectorAll('[data-testid="stMarkdownContainer"]')].map(n => n.innerText).join('\n'),
    }));
    return [...root.querySelectorAll('[data-role="user"], [data-role="assistant"]')].map(n=>({role:n.dataset.role,text:n.innerText,visible:visible(n)}));
  };
  const discover = (field, submit) => {
    const placeholder=field.getAttribute('placeholder');
    if (!placeholder || [...document.querySelectorAll('textarea,input')].filter(n=>n.getAttribute('placeholder')===placeholder).length !== 1) return null;
    if (document.querySelector('[data-testid="stApp"]')) return {version:1,kind:'streamlit',placeholder,submit:'Enter'};
    const logs=[...document.querySelectorAll('[role="log"][aria-label][aria-busy]')];
    if (logs.length!==1 || !submit) return null;
    const name=submit.innerText.trim();
    if (!name || [...document.querySelectorAll('button')].filter(n=>n.innerText.trim()===name).length!==1) return null;
    return {version:1,kind:'accessible_log',placeholder,submit:'button',buttonName:name,logName:logs[0].getAttribute('aria-label')};
  };
  const start = (field, question, submit=null) => {
    if (interaction) { interaction.error='dom_question_ambiguous'; return; }
    const binding=locked || discover(field,submit);
    if (!binding) { interaction={error:'dom_binding_ambiguous'}; return; }
    interaction={question, baseline:messages(binding), binding, beforeTurnId:rootFor(binding)?.getAttribute("data-turn-id"), startedAt:performance.now(), sawBusy:false};
  };
  const expected=()=>marker ?? globalThis.__lladarDomMarker;
  document.addEventListener('keydown', event => {
    const field=event.target, question=expected();
    if (question && document.querySelector('[data-testid="stApp"]') && event.key==='Enter' && !event.shiftKey &&
        field instanceof HTMLTextAreaElement && field.value===question) start(field,question);
  },true);
  document.addEventListener('submit', event => {
    const question=expected();
    if (!question || document.querySelector('[data-testid="stApp"]')) return;
    const fields=[...event.target.querySelectorAll('textarea,input')].filter(n=>n.value===question);
    if (fields.length!==1) { interaction={error:'dom_binding_ambiguous'}; return; }
    start(fields[0],question,event.submitter);
  },true);
  new MutationObserver(records=>{
    if (!interaction || interaction.error) return;
    const root=rootFor(interaction.binding);
    for (const r of records) {
      if (r.target!==root) continue;
      if ((r.attributeName==='aria-busy' && (r.oldValue==='true' || root.getAttribute('aria-busy')==='true')) ||
          (r.attributeName==='data-test-script-state' && (r.oldValue==='running' || root.getAttribute('data-test-script-state')==='running'))) interaction.sawBusy=true;
    }
  }).observe(document,{subtree:true,attributes:true,attributeOldValue:true,attributeFilter:['aria-busy','data-test-script-state']});
  globalThis.__lladarDom={
    bindingReady(binding) { return Boolean(rootFor(binding)); },
    start(value,binding=null) { marker=value; interaction=null; locked=binding; },
    stop() { marker=null; globalThis.__lladarDomMarker=null; locked=null; },
    snapshot() {
      if (!interaction) return null;
      if (interaction.error) return {error:interaction.error};
      const root=rootFor(interaction.binding);
      if (!root) return {error:'dom_binding_lost'};
      const current=messages(interaction.binding), before=interaction.baseline;
      const turnId=root.getAttribute('data-turn-id');
      const replaced=interaction.binding.kind==='accessible_log' && interaction.beforeTurnId!==null && turnId!==null && turnId!=='' && turnId!==interaction.beforeTurnId;
      if (!replaced && (current.length<before.length || before.some((v,i)=>current[i].role!==v.role || current[i].text!==v.text))) return {error:'dom_question_ambiguous'};
      const fresh=replaced ? current : current.slice(before.length);
      if (fresh.some(n=>!n.visible)) return {error:'dom_binding_lost'};
      if (fresh.length>2 || (fresh[0] && (fresh[0].role!=='user' || fresh[0].text!==interaction.question))) return {error:'dom_question_ambiguous'};
      const idle=interaction.binding.kind==='streamlit' ? root.getAttribute('data-test-script-state')==='notRunning' : root.getAttribute('aria-busy')==='false';
      const complete=idle && interaction.sawBusy && fresh.length===2 && fresh[1].role==='assistant' && Boolean(fresh[1].text);
      return {binding:interaction.binding,body:complete ? fresh[1].text : '',complete,elapsedMs:performance.now()-interaction.startedAt};
    },
  };
})();
"""


def snapshot(page):
    return page.evaluate("() => globalThis.__lladarDom?.snapshot() || null")


def observe(page, capture):
    value = snapshot(page)
    if value and value.get("error"):
        raise ExtractionError(value["error"])
    if not value or not value.get("complete"):
        return None
    body = value["body"]
    if len(body.encode("utf-8")) > 1024 * 1024:
        raise BrowserResponseLimitExceeded()
    return value["binding"], capture.observe("text/plain", body, stream_closed=True)


def replay(page, template, question, capture, timeout_ms):
    deadline = time.monotonic() + timeout_ms / 1000
    attempted = False
    owner_page = page
    try:
        if page.url != template.url:
            raise ExtractionError("dom_binding_lost")
        binding = template.dom_binding
        if "frame_name" in binding:
            frames = [frame for frame in page.frames if frame.name == binding["frame_name"]]
            if len(frames) != 1 or frames[0].url != binding["frame_url"]:
                raise ExtractionError("dom_binding_lost")
            page = frames[0]
        if not page.evaluate("b => globalThis.__lladarDom.bindingReady(b)", binding):
            raise ExtractionError("dom_binding_lost")
        field = page.get_by_placeholder(binding["placeholder"], exact=True)
        if field.count() != 1:
            raise ExtractionError("dom_binding_lost")
        button = None
        if binding["submit"] == "button":
            button = page.get_by_role("button", name=binding["buttonName"], exact=True)
            if button.count() != 1:
                raise ExtractionError("dom_binding_lost")
        page.evaluate("o => globalThis.__lladarDom.start(o.question,o.binding)", {"question":question,"binding":binding})
        field.fill(question, timeout=timeout_ms)
        remaining = max(1, int((deadline-time.monotonic())*1000))
        attempted = True
        if button is not None:
            button.click(timeout=remaining)
        else:
            field.press(binding["submit"], timeout=remaining)
        while time.monotonic() < deadline:
            owner_page.wait_for_timeout(min(25, timeout_ms))
            result = observe(page, capture)
            if result:
                return result[1]
        raise ExtractionError("dom_completion_unconfirmed")
    except ExtractionError as error:
        raise BrowserDomFailure(error.reason, request_attempted=attempted) from None
    except BrowserResponseLimitExceeded:
        raise
    except Exception:
        raise BrowserDomFailure("dom_submission_failed", request_attempted=attempted) from None
    finally:
        import sys
        pending_error = sys.exc_info()[0] is not None
        try:
            page.evaluate("() => globalThis.__lladarDom.stop()")
        except Exception:
            if not pending_error:
                raise BrowserDomFailure("dom_binding_lost", request_attempted=attempted) from None
