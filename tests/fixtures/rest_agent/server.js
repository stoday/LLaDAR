const http = require('node:http');
const fs = require('node:fs');
const {spawn} = require('node:child_process');

const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const record = (event, detail = '') => fs.appendFileSync('trace.jsonl', JSON.stringify({event, detail}) + '\n');
const children = new Set();
const server = http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, {'Content-Type': 'application/json'});
    res.end(JSON.stringify({ready: true}));
    return;
  }
  if (req.method !== 'POST' || req.url !== '/api/chat') {
    res.writeHead(404); res.end(); return;
  }
  let body = '';
  req.on('data', chunk => body += chunk);
  req.on('end', () => {
    let request;
    try {
      request = JSON.parse(body);
      if (!request.request_id || typeof request.input?.text !== 'string') throw Error('invalid');
    } catch {
      res.writeHead(400); res.end('Invalid request'); return;
    }
    record('http_received', request.request_id);
    const child = spawn(option('--python', 'python'), ['worker.py'], {stdio: ['pipe', 'pipe', 'pipe']});
    children.add(child);
    let output = '';
    child.stdout.on('data', chunk => output += chunk);
    child.stderr.on('data', chunk => process.stderr.write(chunk));
    child.on('error', error => {res.writeHead(500); res.end('Worker start failed');});
    child.on('close', code => {
      children.delete(child);
      if (res.writableEnded) return;
      if (code !== 0) {res.writeHead(500); res.end('Worker failed'); return;}
      try {
        const answer = 'API客服：' + JSON.parse(output).answer;
        record('http_postprocessed', request.request_id);
        res.writeHead(200, {'Content-Type': 'application/json; charset=utf-8'});
        res.end(JSON.stringify({request_id: request.request_id, data: {answer}}));
      } catch {res.writeHead(500); res.end('Invalid worker response');}
    });
    child.stdin.end(JSON.stringify({question: request.input.text}));
  });
});
server.listen(Number(option('--port', '8080')), option('--host', '127.0.0.1'), () => record('server_started'));
process.on('SIGTERM', () => {
  for (const child of children) child.kill();
  server.close(() => process.exit(0));
});
