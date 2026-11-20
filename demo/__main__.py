"""python -m demo [serve|build] ..."""

import sys


def main():
    args = sys.argv[1:]
    if args and args[0] == "build":
        sys.argv = [sys.argv[0], *args[1:]]
        from .build import main as build_main

        build_main()
        return
    if args and args[0] == "serve":
        args = args[1:]
    from .server import main as serve_main

    serve_main(args)


if __name__ == "__main__":
    main()
