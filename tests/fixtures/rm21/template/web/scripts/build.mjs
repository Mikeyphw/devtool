import { mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const application = 'export function answer() { return 42; }\n';
await mkdir('dist', { recursive: true });
await writeFile('dist/app.js', application);
const digest = createHash('sha256').update(application).digest('hex');
await writeFile('dist/manifest.json', JSON.stringify({ files: { 'app.js': digest } }) + '\n');
