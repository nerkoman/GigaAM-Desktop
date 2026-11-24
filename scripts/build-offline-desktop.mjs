import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const release = JSON.parse(readFileSync(new URL('../sidecar/desktop-release.json', import.meta.url)));
function run(command, args) {
  const child = spawnSync(command, args, {
    cwd: root, stdio: 'inherit', env: { ...process.env, PYTHONUTF8: '1', GIGAAM_BUILD_ID: release.build }
  });
  if (child.error) throw child.error;
  if (child.status !== 0) process.exit(child.status ?? 1);
}
run(process.platform === 'win32' ? 'python' : 'python3', ['scripts/stage-desktop-runtime.py']);
run(process.execPath, ['node_modules/vite/bin/vite.js', 'build']);
