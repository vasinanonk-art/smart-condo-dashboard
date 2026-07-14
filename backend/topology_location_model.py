"""Physical-site metadata and data-dependency corrections for topology."""
from backend import topology_runtime

# Electricity is physically polled by the condo TinkerBoard local bridge.
# Tuya and PM2.5 remain condo devices even when Home Assistant is their data source.
topology_runtime.DEPENDENCIES["electricity"] = ["tinkerboard"]
topology_runtime.NODE_LABELS["electricity"] = "Digital Meter"
