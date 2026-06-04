"""Verify: after reload, rule requiresConfirmation=False means NO inference confirm."""
import requests, json, time, threading, sys

BASE = 'http://localhost:9001'

# Reload first
print('[0] Reloading ontology...')
requests.post(f'{BASE}/api/ontology/reload')

# Create conversation
resp = requests.post(f'{BASE}/api/conversations', json={
    'agent_name': 'factory_agent', 'title': 'test-reload-no-inference-confirm'
})
conv_id = resp.json()['id']
print(f'[1] Conversation: {conv_id}')

events = []
stream_done = threading.Event()

def read_stream():
    try:
        resp = requests.post(f'{BASE}/api/messages/stream', json={
            'conversation_id': conv_id,
            'content': 'WO-20250521-001不合格',
            'agent_name': 'factory_agent'
        }, stream=True, timeout=120)
        for line in resp.iter_lines(decode_unicode=True):
            if line and line.startswith('data: '):
                d = line[6:]
                if d == '[DONE]': events.append(('DONE','')); break
                try:
                    obj = json.loads(d)
                    events.append((obj.get('type','?'), obj.get('content','')))
                except: pass
    except Exception as e:
        events.append(('error', str(e)))
    finally:
        stream_done.set()

t = threading.Thread(target=read_stream, daemon=True)
t.start()

# Wait for action confirm_required
time.sleep(8)

# Find first confirm_required
action_confirm_data = None
for typ, content in events:
    if typ == 'confirm_required':
        action_confirm_data = json.loads(content) if isinstance(content, str) else content
        print(f'[2] Action confirm_required: type={action_confirm_data.get("type","action")}')
        print(f'    Params: {json.dumps(action_confirm_data.get("params",{}), ensure_ascii=False)[:200]}')
        break

if not action_confirm_data:
    print('[FAIL] No action confirm_required!')
    for typ, content in events:
        print(f'  {typ}: {str(content)[:150]}')
    sys.exit(1)

# Confirm the action with params
print(f'[3] Confirming action...')
cresp = requests.post(f'{BASE}/api/messages/confirm/{conv_id}', json={
    'approved': True,
    'params': action_confirm_data.get('params', {}),
})
print(f'    Response: {cresp.status_code} {cresp.text}')

# Wait and check if inference confirmation fires
time.sleep(10)

inference_confirm_count = 0
for typ, content in events:
    if typ == 'confirm_required':
        data = json.loads(content) if isinstance(content, str) else content
        if data.get('type') == 'inference_chain':
            inference_confirm_count += 1
            print(f'[4] INFERENCE confirm_required FIRED (UNEXPECTED!)')
        elif not action_confirm_data or data != action_confirm_data:
            # Another non-inference confirm
            pass

# Check tool_result for inference_preview
tool_results = [(typ, content) for typ, content in events if typ == 'tool_result']
print(f'\n=== Tool Results ({len(tool_results)}) ===')
for typ, content in tool_results:
    print(f'  {content}')

if inference_confirm_count == 0:
    print('\n[PASS] No inference confirmation — rule.requiresConfirmation=False works!')
else:
    print(f'\n[FAIL] {inference_confirm_count} inference confirmations fired unexpectedly!')

stream_done.wait(timeout=10)
print('\n=== All Events ===')
for typ, content in events:
    print(f'  {typ}: {str(content)[:200]}')
