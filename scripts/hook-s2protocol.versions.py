from pathlib import Path

import s2protocol


versions_dir = Path(s2protocol.__file__).resolve().parent / "versions"
protocol_files = sorted(versions_dir.glob("protocol[0-9]*.py"))
if not protocol_files:
    raise RuntimeError(f"No s2protocol version modules found in {versions_dir}")

datas = [(str(path), "s2protocol/versions") for path in protocol_files]
