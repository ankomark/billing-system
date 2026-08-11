#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

    # Tests get their own cache — see the CACHES block in settings.py for why.
    # It is set here rather than there because settings cannot tell: under
    # `--parallel` the workers are spawned processes whose sys.argv is the
    # multiprocessing bootstrap, not this command line. The environment is
    # inherited by those workers, so this reaches them and sys.argv would not.
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        os.environ.setdefault('RUNNING_TESTS', '1')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
