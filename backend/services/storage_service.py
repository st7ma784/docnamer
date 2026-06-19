import shutil

from config import DATA_DIR, OUTPUT_DIR


def get_disk_usage() -> dict:
    total, used, free = shutil.disk_usage(DATA_DIR)
    output_bytes = sum(f.stat().st_size for f in OUTPUT_DIR.rglob("*") if f.is_file())
    return {
        "disk_total": total,
        "disk_used": used,
        "disk_free": free,
        "output_bytes": output_bytes,
    }
