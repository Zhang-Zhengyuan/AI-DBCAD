// Cross-platform isolated backend for the real-worker browser tests.
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const backend = path.join(root, 'CAD_DB/backend');
const python = process.env.CAD_DB_E2E_PYTHON || path.join(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'dbcad-e2e-'));
const child = spawn(python, ['-m', 'uvicorn', '--app-dir', backend, 'app.main:app', '--host', '127.0.0.1', '--port', '8765', '--log-level', 'warning'], {
  cwd: work, stdio: 'inherit',
  env: { ...process.env, CAD_DB_COMPONENT_ROOT: path.join(work, 'components') },
});
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => child.kill(signal));
child.on('error', error => { console.error(error); process.exitCode = 1; });
child.on('close', code => { fs.rmSync(work, { recursive: true, force: true }); process.exit(code ?? 1); });
