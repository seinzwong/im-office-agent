conda activate feishu
npm install
npm run all
npm run all:variant
npm run agent:patch
node src/index.mjs all --ir data/ir.agent.generated.json --out out/agent
Get-ChildItem out -Recurse
