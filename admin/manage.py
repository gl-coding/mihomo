#!/usr/bin/env python3
import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mihomo_site.settings")
    admin_dir = os.path.dirname(os.path.abspath(__file__))
    if admin_dir not in sys.path:
        sys.path.insert(0, admin_dir)
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
