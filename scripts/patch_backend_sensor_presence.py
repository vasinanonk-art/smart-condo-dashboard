from pathlib import Path

p = Path('/opt/smart-condo-dashboard-run/backend/app.py')
s = p.read_text(encoding='utf-8')

s = s.replace(
    'client.subscribe(MQTT_STATE_TOPIC)',
    'client.subscribe(MQTT_STATE_TOPIC)\n    client.subscribe("condo/t3/state")\n    client.subscribe("condo/presence/beer")\n    client.subscribe("condo/presence/seem")'
)

s = s.replace(
    '    "light_status_cache": {},\n',
    '    "light_status_cache": {},\n    "sensor": {},\n    "presence": {},\n'
)

old = '''def on_message(client, userdata, msg):
    payload = msg.payload.decode(errors="ignore")
    try:
        state["last_state"] = json.loads(payload)
    except Exception:
        state["last_state"] = {"raw": payload}
    state["last_state_topic"] = msg.topic
    state["last_state_ts"] = int(time.time())'''

new = '''def on_message(client, userdata, msg):
    payload = msg.payload.decode(errors="ignore")
    try:
        parsed = json.loads(payload)
    except Exception:
        parsed = {"raw": payload}

    state["last_state"] = parsed
    state["last_state_topic"] = msg.topic
    state["last_state_ts"] = int(time.time())

    if msg.topic == "condo/t3/state" and isinstance(parsed, dict):
        state["sensor"] = parsed | {"ts": int(time.time())}
    elif msg.topic.startswith("condo/presence/") and isinstance(parsed, dict):
        name = msg.topic.rsplit("/", 1)[-1]
        state["presence"][name] = parsed | {"ts": int(time.time())}'''

if old in s:
    s = s.replace(old, new)

api_marker = '@app.get("/api/state")\ndef get_state():\n    return state\n'
api_add = '''@app.get("/api/condo/status")
def condo_status():
    return {"ok": True, "sensor": state.get("sensor", {}), "presence": state.get("presence", {})}


'''
if '/api/condo/status' not in s and api_marker in s:
    s = s.replace(api_marker, api_marker + '\n\n' + api_add)

p.write_text(s, encoding='utf-8')
