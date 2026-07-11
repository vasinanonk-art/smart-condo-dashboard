import threading
import time

import sonoff_client as presence_automation
from backend import app as app_module
from backend import dashboard_extensions

PRESENCE_EVALUATION_SEC = 5

_seen_home_since_start = {person: False for person in presence_automation.ARRIVAL_PEOPLE}
_original_run_arrival_automation = presence_automation._run_arrival_automation


def _guarded_run_arrival_automation(presence):
    """Ignore an Away startup baseline until that person is observed Home once."""
    presence = presence if isinstance(presence, dict) else {}
    for person in presence_automation.ARRIVAL_PEOPLE:
        item = presence.get(person)
        if presence_automation._is_arrived_home(item):
            _seen_home_since_start[person] = True
        if not _seen_home_since_start[person]:
            continue
        presence_automation._run_person_arrival_automation(person, presence)


presence_automation._run_arrival_automation = _guarded_run_arrival_automation


def _presence_worker():
    # Keep Router/Ping presence evaluations independent from dashboard refreshes
    # and from whether MQTT publishes a new presence message.
    while True:
        try:
            presence_automation._resolve_store_presence(app_module, evaluate=True)
        except Exception as exc:
            # Meaningful transition logs remain in sonoff_client; this is only a
            # safe worker-level failure signal and contains no credentials.
            print(f"automation presence worker error: {type(exc).__name__}", flush=True)
        time.sleep(PRESENCE_EVALUATION_SEC)


def _reliable_zone_apply(device, body, preset):
    target = app_module.device_target(device)
    action = body.action.strip().lower()

    if action == "brightness":
        command = app_module.LightCommand(
            target=target,
            action="brightness",
            value=max(10, min(1000, int(body.value))),
        )
        app_module.apply_light_all_once(device, command)
        return

    if action in ("temperature", "temp", "cct"):
        command = app_module.LightCommand(
            target=target,
            action="temperature",
            value=max(0, min(1000, int(body.value))),
        )
        app_module.apply_light_all_once(device, command)
        return

    if action == "rgb":
        command = app_module.LightCommand(
            target=target,
            action="rgb",
            h=max(0, min(360, int(body.h))),
            s=max(0, min(1000, int(body.s))),
            v=max(0, min(1000, int(body.v))),
        )
        app_module.apply_light_all_once(device, command)
        return

    if action == "preset" and preset:
        mode = preset["mode"]
        if mode == "white":
            app_module.apply_light_all_once(
                device,
                app_module.LightCommand(
                    target=target,
                    action="brightness",
                    value=preset["brightness"],
                ),
            )
            app_module.apply_light_all_once(
                device,
                app_module.LightCommand(
                    target=target,
                    action="temperature",
                    value=preset["temperature"],
                ),
            )
            return
        if mode == "brightness":
            app_module.apply_light_all_once(
                device,
                app_module.LightCommand(
                    target=target,
                    action="brightness",
                    value=preset["value"],
                ),
            )
            return
        if mode == "temperature":
            app_module.apply_light_all_once(
                device,
                app_module.LightCommand(
                    target=target,
                    action="temperature",
                    value=preset["value"],
                ),
            )
            return
        if mode == "colour":
            app_module.apply_light_all_once(
                device,
                app_module.LightCommand(
                    target=target,
                    action="rgb",
                    h=preset["h"],
                    s=preset["s"],
                    v=preset["v"],
                ),
            )
            return

    raise dashboard_extensions.HTTPException(status_code=400, detail="unsupported zone action")


dashboard_extensions._apply_to_device = _reliable_zone_apply


@app_module.app.on_event("startup")
def start_presence_automation_worker():
    if getattr(app_module, "_presence_automation_worker_started", False):
        return
    app_module._presence_automation_worker_started = True
    worker = threading.Thread(
        target=_presence_worker,
        name="presence-automation-worker",
        daemon=True,
    )
    worker.start()
