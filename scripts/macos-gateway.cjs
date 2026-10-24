// Local convenience gateway, matching the TeamKit's password-injection model.
// The authenticated backend remains separate; both bind only to loopback.
const http = require('node:http');
const net = require('node:net');
const fs = require('node:fs');
const path = require('node:path');
const settings = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../.local/macos/settings.json'), 'utf8'));
const backend = settings.ports.backend;
const headers = req => ({ ...req.headers, host: `127.0.0.1:${backend}`, 'x-api-password': settings.api_password });
const server = http.createServer((req, res) => {
  const upstream = http.request({ host: '127.0.0.1', port: backend, path: req.url, method: req.method, headers: headers(req) }, response => {
    res.writeHead(response.statusCode, response.headers); response.pipe(res);
  });
  upstream.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end('DBCAD backend unavailable'); });
  req.on('aborted', () => upstream.destroy());
  req.pipe(upstream);
});
server.on('upgrade', (req, socket, head) => {
  const upstream = net.connect(backend, '127.0.0.1', () => {
    upstream.write(`${req.method} ${req.url} HTTP/1.1\r\n` + Object.entries(headers(req)).map(([k, v]) => `${k}: ${v}`).join('\r\n') + '\r\n\r\n');
    if (head.length) upstream.write(head);
    socket.pipe(upstream).pipe(socket);
  });
  upstream.on('error', () => socket.destroy());
  socket.on('error', () => upstream.destroy());
  socket.on('close', () => upstream.destroy());
});
server.listen(settings.ports.web, '127.0.0.1', () => console.log(`DBCAD: http://127.0.0.1:${settings.ports.web}/web/`));
