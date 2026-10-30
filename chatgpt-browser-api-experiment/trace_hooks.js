(() => {
  if (window.__sourceTrace) return;
  const trace = window.__sourceTrace = {fetches: [], random: [], sdk: []};
  const stack = () => String(new Error().stack || '');
  const wrapped = new WeakSet();
  setInterval(() => {
    for (const name of Object.getOwnPropertyNames(window).filter(x=>/sentinel/i.test(x))) {
      let sdk; try {sdk=window[name];} catch {continue;}
      if (!sdk || !['object','function'].includes(typeof sdk)) continue;
      for (const key of Object.getOwnPropertyNames(sdk)) {
        if (!/^get.*(?:Token|Requirements)/i.test(key)) continue;
        const fn=sdk[key];
        if (typeof fn!=='function' || wrapped.has(fn)) continue;
        const hook=function(...args) {
          const at=Date.now(), caller=stack(), result=fn.apply(this,args);
          const record=value=>{if(trace.sdk.length<150) trace.sdk.push({name:name+'.'+key,at,value,stack:caller});};
          if (result?.then) result.then(record,()=>{}); else record(result);
          return result;
        };
        wrapped.add(hook); try {sdk[key]=hook;} catch {}
      }
    }
  }, 25);
  const nativeUUID = crypto.randomUUID?.bind(crypto);
  if (nativeUUID) crypto.randomUUID = function() {
    const value = nativeUUID();
    if (trace.random.length < 800) trace.random.push({kind:'randomUUID', value, at:Date.now(), stack:stack()});
    return value;
  };
  const nativeRandom = crypto.getRandomValues.bind(crypto);
  crypto.getRandomValues = function(array) {
    const value = nativeRandom(array);
    if (array instanceof Uint8Array && array.length === 16 && trace.random.length < 800) {
      const bytes = Array.from(array);
      bytes[6] = (bytes[6] & 15) | 64;
      bytes[8] = (bytes[8] & 63) | 128;
      const hex = bytes.map(x=>x.toString(16).padStart(2,'0')).join('');
      const uuid = [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
      trace.random.push({kind:'getRandomValues-v4-uuid', value:uuid, at:Date.now(), stack:stack()});
    }
    return value;
  };
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async function(...args) {
    const started = Date.now();
    const response = await nativeFetch(...args);
    const url = new URL(response.url, location.href);
    if (/\/conversation\/|\/sentinel\//.test(url.pathname) && trace.fetches.length < 100) {
      const item = {path:url.pathname, host:url.hostname, started, received:Date.now(),
        status:response.status, contentType:response.headers.get('content-type'), body:'', done:false};
      trace.fetches.push(item);
      const reader = response.clone().body?.getReader(), decoder = new TextDecoder();
      if (reader) (async()=>{try {while (true) {
        const {value,done} = await reader.read();
        if (done) {item.body+=decoder.decode(); break;}
        item.body+=decoder.decode(value,{stream:true});
      }} catch {item.error=true;} finally {item.finished=Date.now(); item.done=true;}})();
      else item.done=true;
    }
    return response;
  };
})();
