"""
Verify all API keys are loaded correctly from Admin/.env.
Run: python scripts/verify_keys.py
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.key_loader import get_all_keys

print("\n=== Finboard — Key Verification ===\n")

keys = get_all_keys()
all_ready = True

for name, masked in keys.items():
    status = "READY" if masked != "(not set)" else "MISSING"
    icon = "[OK]" if status == "READY" else "[!!]"
    print(f"  {icon} {name:25s} {status:8s}  {masked}")
    if status == "MISSING":
        all_ready = False

print()
if all_ready:
    print("All keys loaded successfully.")
else:
    print("Some keys are missing. Check Admin/.env")
print()
