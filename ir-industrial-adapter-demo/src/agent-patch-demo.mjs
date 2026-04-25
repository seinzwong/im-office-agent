import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, '..');
const input = path.join(ROOT, 'data', 'ir.json');
const output = path.join(ROOT, 'data', 'ir.agent.generated.json');

const ir = JSON.parse(await fs.readFile(input, 'utf8'));

// 模拟 Agent 的输出：只改 IR，不改任何 Adapter 代码。
// 生产系统建议让 Agent 输出 JSON Patch；这里为了可读性直接修改对象。
ir.meta.title = 'Agent 自动改写版：IR 驱动的精美交付物';
ir.theme = {
  ...ir.theme,
  name: 'AI Purple',
  accent: '#7C3AED',
  accent2: '#2F6BFF',
  background: '#FAF5FF',
  muted: '#6B7280'
};

const cards = ir.blocks.find(b => b.id === 'capabilities');
if (cards) {
  cards.cards.push({
    title: 'Evidence',
    body: '每条结论可绑定来源、置信度、确认人和修订历史。'
  });
}

const risks = ir.blocks.find(b => b.id === 'risks');
if (risks) {
  risks.rows.push([
    '模型幻觉导致结论错误',
    '重要结论必须绑定 evidence，并在发布前走人工确认门禁'
  ]);
}

await fs.writeFile(output, JSON.stringify(ir, null, 2), 'utf8');
console.log(`wrote ${path.relative(ROOT, output)}`);
console.log('Run: node src/index.mjs all --ir data/ir.agent.generated.json --out out/agent');
