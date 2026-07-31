import sqlite3, json

local = sqlite3.connect('D:/code/long-running-agent-harness/projects/factory-copilot/backend/data/agent.db')
local.row_factory = sqlite3.Row

server_chains = ['fault_diagnosis','production_report','quality_analysis','work_order_readiness']
all_chains = [r['chain_id'] for r in local.execute('SELECT chain_id FROM agent_chains')]
missing = [c for c in all_chains if c not in server_chains]
print(f'Missing: {missing}')

chains_data = []
steps_data = []

for cid in missing:
    c = dict(local.execute('SELECT * FROM agent_chains WHERE chain_id=?', (cid,)).fetchone())
    chains_data.append(c)

    steps = local.execute('SELECT * FROM agent_chain_steps WHERE chain_id=? ORDER BY step_order', (cid,)).fetchall()
    for s in steps:
        d = dict(s)
        d['action_name'] = d.get('action_name') or ''
        d['action_params'] = d.get('action_params') or '{}'
        d['precondition'] = d.get('precondition') or ''
        d['on_failure'] = d.get('on_failure') or 'skip'
        steps_data.append(d)
    print(f'  {cid}: {len(steps)} steps')

local.close()

with open('D:/tmp/chains_sync.json', 'w') as f:
    json.dump(chains_data, f, default=str, indent=2)

with open('D:/tmp/steps_sync.json', 'w') as f:
    json.dump(steps_data, f, default=str, indent=2)

print(f'Done: {len(chains_data)} chains, {len(steps_data)} steps')
