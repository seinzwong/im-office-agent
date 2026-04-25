#!/usr/bin/env bash
set -euo pipefail
npm install
npm run all
npm run all:variant
npm run agent:patch
node src/index.mjs all --ir data/ir.agent.generated.json --out out/agent
find out -maxdepth 3 -type f | sort
