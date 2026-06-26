"""Helper script to run mx_search and save output to UTF-8 file."""
import subprocess
import sys
import os

query = sys.argv[1] if len(sys.argv) > 1 else "A股市场最新新闻"
output_dir = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(output_dir, exist_ok=True)

script = os.path.join(os.path.dirname(__file__), "mx_search.py")
result = subprocess.run(
    [sys.executable, script, query, output_dir],
    capture_output=True,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
)

out_file = os.path.join(output_dir, "result.txt")
with open(out_file, "w", encoding="utf-8") as f:
    f.write(result.stdout.decode("utf-8", errors="replace"))
    f.write("\n--- STDERR ---\n")
    f.write(result.stderr.decode("utf-8", errors="replace"))
    f.write(f"\n--- EXIT CODE: {result.returncode} ---")

print(f"Output saved to: {out_file}")
