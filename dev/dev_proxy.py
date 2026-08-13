"""dev-proxy — Global CLI wrapper for isolated dev proxy."""

import os
import subprocess
import sys


def main():
    """Entry point for the dev-proxy command."""
    # Resolve path to the dev-proxy.sh script relative to this file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    wrapper = os.path.join(script_dir, 'dev-proxy.sh')

    if not os.path.exists(wrapper):
        # Try parent directory (for installed package)
        parent = os.path.dirname(script_dir)
        wrapper = os.path.join(parent, 'dev', 'dev-proxy.sh')

    if not os.path.exists(wrapper):
        print(f"Error: dev-proxy.sh not found at {wrapper}", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(['bash', wrapper] + sys.argv[1:])
    sys.exit(result.returncode)


if __name__ == '__main__':
    main()
