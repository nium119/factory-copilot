"""活体双确认流测试：Action 确认 → 推理确认（打本机 FC，服务在线才跑）。"""
import requests
import json
import time
import threading
import sys
import urllib.request
import pytest

from app.core.config import settings

BASE = 'http://127.0.0.1:9004'

# FC API 需 Bearer JWT（admin 测试身份）
import jwt

_TOKEN = jwt.encode(
    {"EmpCode": "admin", "LoginUserName": "admin", "exp": int(time.time()) + 3600},
    settings.JWT_SECRET, algorithm="HS256",
)
HEADERS = {"Authorization": f"Bearer {_TOKEN}"}


def test_dual_confirmation():
    """Test the full dual-confirmation flow."""
    # 门禁：FC 服务不在线时跳过（活体测试不阻塞 CI）
    try:
        urllib.request.urlopen(BASE + '/health', timeout=3)
    except Exception:
        pytest.skip('FC 服务未启动（9004），跳过活体双确认测试')

    # 1) Create conversation
    resp = requests.post(f'{BASE}/api/conversations', json={
        'agent_name': 'factory_agent',
        'title': 'test-inference-e2e'
    }, headers=HEADERS)
    assert resp.status_code == 200, f"Create conversation failed: {resp.text}"
    conv = resp.json()
    conv_id = conv.get('id', '')
    print(f'[1] Conversation: {conv_id}')

    # 2) Start SSE stream
    events = []
    stream_done = threading.Event()

    def read_stream():
        try:
            resp = requests.post(f'{BASE}/api/messages/stream', json={
                'conversation_id': conv_id,
                'content': 'WO-20250521-001不合格',
                'agent_name': 'factory_agent'
            }, headers=HEADERS, stream=True, timeout=120)
            for line in resp.iter_lines(decode_unicode=True):
                if line and line.startswith('data: '):
                    data_str = line[6:]
                    if data_str == '[DONE]':
                        events.append(('[DONE]', ''))
                        break
                    try:
                        obj = json.loads(data_str)
                        events.append((obj.get('type', '?'), obj.get('content', '')))
                        print(f'  SSE: {obj.get("type")} {str(obj.get("content"))[:150]}')
                    except json.JSONDecodeError:
                        events.append(('raw', data_str))
        except Exception as e:
            events.append(('error', str(e)))
            print(f'  SSE ERROR: {e}')
        finally:
            stream_done.set()

    t = threading.Thread(target=read_stream, daemon=True)
    t.start()

    # 3) Wait for first confirm_required (Action) and relay its params back
    time.sleep(5)
    found_action_confirm = False
    found_inference_confirm = False
    action_params = {}

    # Extract action confirm params
    for _ in range(20):
        time.sleep(1)
        for typ, content in events:
            if typ == 'confirm_required' and not found_action_confirm:
                data = json.loads(content) if isinstance(content, str) else content
                ctype = data.get('type', '')
                # First confirm is the action (type=None or type='write' for old actions)
                if ctype != 'inference_chain':
                    print(f'\n[2] Action confirm_required: type={ctype}')
                    print(f'    Params: {json.dumps(data.get("params", {}), ensure_ascii=False)}')
                    action_params = data.get('params', {})
                    found_action_confirm = True

                    # Relay the same params back (as frontend would)
                    print(f'[3] Sending confirm with params...')
                    cresp = requests.post(f'{BASE}/api/messages/confirm/{conv_id}', json={
                        'approved': True,
                        'params': action_params,
                    }, headers=HEADERS)
                    print(f'[3] Confirm response: {cresp.status_code} {cresp.text}')
                    break
        if found_action_confirm:
            break

    if not found_action_confirm:
        print('\n[FAIL] Action confirmation NOT received!')
        for typ, content in events:
            print(f'  Event: {typ} {str(content)[:150]}')
        stream_done.wait(timeout=10)
        return False

    # 4) Wait for second confirm_required (Inference)
    print(f'\n[4] Waiting for inference confirmation...')
    for _ in range(30):
        time.sleep(1)
        for typ, content in events:
            if typ == 'confirm_required':
                data = json.loads(content) if isinstance(content, str) else content
                if data.get('type') == 'inference_chain':
                    print(f'[5] Inference confirm_required received!')
                    print(f'    Inferences: {json.dumps(data.get("inferences", []), indent=2, ensure_ascii=False)}')
                    found_inference_confirm = True

                    # Confirm the inference
                    print(f'[6] Confirming inference (approved=True)...')
                    cresp = requests.post(f'{BASE}/api/messages/confirm/{conv_id}', json={
                        'approved': True,
                        'params': {},
                    }, headers=HEADERS)
                    print(f'[6] Confirm response: {cresp.status_code} {cresp.text}')
                    break
            elif typ == 'execution_done':
                print(f'  execution_done: {content}')
                break
        if found_inference_confirm:
            break
        if any(e[0] == 'execution_done' for e in events):
            break

    # 5) Wait for stream to finish
    stream_done.wait(timeout=15)

    print(f'\n=== All events ({len(events)}) ===')
    for typ, content in events:
        print(f'  {typ}: {str(content)[:200]}')

    if found_inference_confirm:
        print('\n[PASS] Dual confirmation flow works!')
        return True
    else:
        print('\n[FAIL] Inference confirmation NOT triggered!')
        for typ, content in events:
            if typ == 'tool_result':
                print(f'  tool_result: {content}')
        return False


if __name__ == '__main__':
    success = test_dual_confirmation()
    sys.exit(0 if success else 1)
